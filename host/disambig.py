"""The optional pre-render pass: a model answers the two-reading words.

A word like "live" has two readings and the sentence decides which one. Asking
the reader is right for a public box (nobody should pay for a voice that gets
their book wrong), but it is the wrong shape for the one person this exists for
right now: she wants to press play.

So this pass runs the same questions through the same plugin, takes the answers,
and stores them where pronunciation already lives: a book-scoped dictionary. The
rules are ordinary rules from then on, so the engine speaks the right reading on
the first render, a re-render re-uses the cached audio, the reader can see and
edit what the model chose, and nobody pays the model twice for one book.

It is OFF unless an account is named in FIRESIDE_DISAMBIG_ACCOUNTS. A render on
any other account does not even look at it: no call, no latency, no charge.
"""
import os
import re
import time

from host import pluginhost, pron

DEFAULT_PLUGINS_ROOT = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "plugins")
DICT_NAME = "Readings (model)"
_SCOPE = "book"
_WORD = re.compile(r"^[A-Za-z][A-Za-z'’-]{1,30}$")
# a respelling is one to four ordinary words ("liv", "lyve", "berry good")
_SAY = re.compile(r"^[A-Za-z][A-Za-z'’-]*(?:\s+[A-Za-z][A-Za-z'’-]*){0,3}$")


def _env(name):
    return (os.environ.get(name) or "").strip()


def accounts():
    """The account ids this is allowed to run for. Empty means nobody."""
    return {a.strip() for a in _env("FIRESIDE_DISAMBIG_ACCOUNTS").split(",") if a.strip()}


def model():
    return _env("FIRESIDE_DISAMBIG_MODEL")


def base_url():
    return _env("FIRESIDE_DISAMBIG_URL")


def key():
    """The bearer token a hosted server wants. Empty is legal: a box on the LAN
    takes no key, and the plugin treats an empty string as "send no header"."""
    return _env("FIRESIDE_DISAMBIG_KEY")


def configured():
    return bool(base_url() and model())


def enabled_for(uid):
    """Off for everyone, unless this exact account is named and the box is set."""
    return bool(uid) and configured() and uid in accounts()


def _plugin(plugins_root=None):
    plugins_root = plugins_root or DEFAULT_PLUGINS_ROOT
    for pid, p in pluginhost.discover("disambig", plugins_root).items():
        if getattr(p.obj, "decide", None):
            return p.obj
    return None


def existing(conn, uid, book_id):
    """The dictionary this pass already wrote for a book, if it did."""
    for d in pron.dicts(conn, uid):
        if d.get("scope") == _SCOPE and d.get("scope_key") == book_id \
                and d.get("name") == DICT_NAME:
            return d
    return None


# One call per this many characters of candidate text. A hosted model meters
# tokens per minute, not just per token: a 2.6M-char book is ~650k tokens and no
# deployment will take that in one request (ours allows 50k/min), so the pass is
# batched and paced the same way a human would read it.
BATCH_CHARS = 24000
BATCH_PAUSE = 1.0        # seconds between requests (tests set this to 0)
RETRY_BACKOFF = 5.0      # seconds before retrying a rate limit
_TRANSIENT = ("(429)", "did not answer")

def _batches(texts, limit=BATCH_CHARS):
    """Chapter texts gathered into requests that fit a rate limit."""
    buf, size = [], 0
    for t in texts:
        if buf and size + len(t) > limit:
            yield "\n".join(buf)
            buf, size = [], 0
        buf.append(t)
        size += len(t)
    if buf:
        yield "\n".join(buf)


def _call_batched(call, texts, tries=3):
    """Every batch, with a short backoff on a rate limit.

    Returns (fixes, failed, total, last_error).
    """
    fixes, failed, last = [], 0, ""
    batches = list(_batches(texts))
    for n, batch in enumerate(batches):
        if n:
            # the meter is per minute; a steady drip stays under it
            time.sleep(BATCH_PAUSE)
        for attempt in range(tries):
            try:
                fixes.extend(call(batch, model(),
                                  {"base_url": base_url(), "key": key()}) or [])
                break
            except Exception as e:
                transient = any(s in str(e) for s in _TRANSIENT)
                if transient and attempt < tries - 1:
                    time.sleep(RETRY_BACKOFF * (attempt + 1))
                    continue
                last = "%s: %s" % (type(e).__name__, e)
                failed += 1
                break
    return fixes, failed, len(batches), last


def _rules(book_id, fixes):
    """Model answers as pronunciation rules, scoped to this one book."""
    out, seen = [], set()
    for f in fixes or []:
        word, say = (f.get("word") or "").strip(), (f.get("say") or "").strip()
        if not word or not say or word.lower() in seen:
            continue
        # a respelling is a spelling, not a sentence: refuse anything that would
        # turn one word into prose the engine has to guess at
        if not _WORD.match(word) or not _SAY.match(say) or len(say) > 40:
            continue
        if say.lower() == word.lower():
            continue
        seen.add(word.lower())
        out.append(pron._rule(word, say, book=book_id))
    return out


def ensure(conn, storage, uid, book_id, idxs, plugins_root=None, run=None):
    """Make sure this book's readings exist before it is voiced.

    Returns the rules for this book (possibly empty). Never raises: a model box
    that is asleep must not fail somebody's render, so a failure is reported and
    the render continues with the user's own rules.
    """
    if not enabled_for(uid):
        return [], ""
    if existing(conn, uid, book_id):
        return [], ""
    text = []
    for i in idxs or []:
        try:
            row = conn.execute("SELECT text_ref FROM chapters WHERE book_id=? AND idx=?",
                               (book_id, i)).fetchone()
            if row:
                text.append(storage.get(row["text_ref"]).decode("utf-8"))
        except Exception:
            continue
    if not text:
        return [], ""
    plugin = _plugin(plugins_root)
    if plugin is None:
        return [], "no disambiguator plugin is installed"
    call = run or plugin.decide
    fixes, failed, total, last = _call_batched(call, text)
    rules = _rules(book_id, fixes)
    if failed and not rules:
        # Nothing at all came back: say so and leave no dictionary behind, so the
        # next render tries again instead of finding an empty one and giving up.
        return [], "%d of %d part(s) failed (%s)" % (failed, total, last)
    pron.save_dict(conn, uid, DICT_NAME, _SCOPE, book_id, rules)
    if failed:
        return rules, ("%d of %d part(s) failed (%s); saved what came back"
                       % (failed, total, last))
    return rules, ""
