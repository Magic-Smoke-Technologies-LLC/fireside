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
    try:
        fixes = call("\n".join(text), model(), {"base_url": base_url()})
    except Exception as e:
        return [], "%s: %s" % (type(e).__name__, e)
    rules = _rules(book_id, fixes)
    pron.save_dict(conn, uid, DICT_NAME, _SCOPE, book_id, rules)
    return rules, ""
