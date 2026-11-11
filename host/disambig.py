"""The optional pre-render pass: a model answers the two-reading words.

A word like "live" has two readings and the sentence decides which one. Asking
the reader is right for a public box (nobody should pay for a voice that gets
their book wrong), but it is the wrong shape for the one person this exists for
right now: she wants to press play.

So this pass runs the same questions through the same plugin and keeps the
answers on one row per book. The answers are SUGGESTIONS, never rules: nothing
the model says reaches the audio until the reader taps "use these", because a
guess that arrives as a setting is a silent lie (11-11: a live model turned
"guide" into "guyd" and "living" into "liv-ing" and those landed in the reader's
own word list). Accepting turns them into an ordinary book-scoped dictionary, so
the engine speaks the right reading and a re-render reuses the cached audio. The
row also records that the pass ran, so nobody pays the model twice for one book.

It runs for a book the reader opted in at import (books.model_pass) or for an
account named in FIRESIDE_DISAMBIG_ACCOUNTS, and only when the box is set. Any
other book costs nothing: no call, no latency, no charge.
"""
import json
import os
import re
import time

from host import pluginhost, pron
from host.db import now

DEFAULT_PLUGINS_ROOT = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "plugins")
DICT_NAME = pron.MODEL_DICT_NAME  # one name, so saving words cannot touch it
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


def opted_in(conn, book_id):
    """Did this book ask for the pass when it was imported?

    Per book, not per account: the reader ticked the box on one import, and that
    is the only place the question was asked.
    """
    if not book_id:
        return False
    try:
        row = conn.execute("SELECT model_pass FROM books WHERE id=?",
                           (book_id,)).fetchone()
    except Exception:
        return False
    return bool(row and (row["model_pass"] or 0))


def set_optin(conn, book_id, on):
    conn.execute("UPDATE books SET model_pass=? WHERE id=?",
                 (1 if on else 0, book_id))
    conn.commit()


def enabled_for(conn, uid, book_id=""):
    """Is the pass allowed to run at all for this book?

    Two ways in, and both are somebody saying yes: this book was opted in at
    import, or the account is named on the box (the old deployment's switch, kept
    so a server that already had it does not go quiet after an upgrade).
    """
    if not configured():
        return False
    if uid and uid in accounts():
        return True
    return opted_in(conn, book_id)


def state(conn, book_id):
    """What happened on this book's last pass, or None if it never ran."""
    try:
        row = conn.execute(
            "SELECT status,rules,words,note,job_id,updated_at FROM model_pass"
            " WHERE book_id=?", (book_id,)).fetchone()
    except Exception:
        return None
    if not row:
        return None
    try:
        rules = json.loads(row["rules"] or "[]")
    except Exception:
        rules = []
    return {"status": (row["status"] or "suggested"), "rules": rules
            if isinstance(rules, list) else [],
            "words": int(row["words"] or 0), "note": row["note"] or "",
            "job_id": row["job_id"] or "", "updated_at": row["updated_at"] or ""}


def _save(conn, uid, book_id, status, rules=(), note="", job_id=""):
    """One row per book, overwritten in place. The row is also the receipt that
    the pass ran, so a finished pass is never paid for a second time."""
    rules = list(rules or [])
    blob = json.dumps(rules, ensure_ascii=False)
    stamp = now()
    if state(conn, book_id) is None:
        conn.execute(
            "INSERT INTO model_pass(book_id,user_id,status,rules,words,note,"
            "job_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (book_id, uid or "", status, blob, len(rules), note, job_id,
             stamp, stamp))
    else:
        conn.execute(
            "UPDATE model_pass SET user_id=?,status=?,rules=?,words=?,note=?,"
            "job_id=?,updated_at=? WHERE book_id=?",
            (uid or "", status, blob, len(rules), note, job_id, stamp, book_id))
    conn.commit()
    return state(conn, book_id)


def apply(conn, uid, book_id):
    """The reader said yes: the suggestions become ordinary rules for this book.

    Nothing is voiced here; applying a rule makes audio stale, and the page
    already knows how to ask about re-encoding only the chapters it touches.
    """
    st = state(conn, book_id)
    if not st or not st["rules"]:
        return st, "nothing to apply"
    got = existing(conn, uid, book_id)
    saved = pron.save_dict(conn, uid, DICT_NAME, _SCOPE, book_id, st["rules"],
                           dict_id=got["id"] if got else "")
    if saved is None:
        return st, "could not save the readings"
    return _save(conn, uid, book_id, "accepted", st["rules"], st["note"],
                 st["job_id"]), ""


def discard(conn, uid, book_id):
    """The default. The suggestions stay on record (so the pass is not re-billed)
    and stop applying: a dictionary they already became is emptied, not deleted,
    because its id is what the reader's screen points at."""
    st = state(conn, book_id)
    if not st:
        return None
    if st["status"] == "accepted":
        got = existing(conn, uid, book_id)
        if got:
            pron.save_dict(conn, uid, DICT_NAME, _SCOPE, book_id, [],
                           dict_id=got["id"])
    return _save(conn, uid, book_id, "discarded", st["rules"], st["note"],
                 st["job_id"])


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
MAX_RULES = 60           # one book's worth of two-reading words, not a lexicon
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
    """Model answers as pronunciation rules, scoped to this one book.

    A dictionary is keyed by word, so a book that uses one two-reading word both
    ways ("record" as a verb and as a noun) cannot be fixed by a rule per word:
    the rule would be right in half the places and wrong in the other half. A
    live answer for two sentences came back with both readings of "record", and
    keeping the first one is a silent wrong answer, not a partial fix. So when
    the answers disagree, neither is written and the word is left plain for the
    engine, which says something defensible instead of something confident and
    wrong.

    Returns (rules, conflicting words).
    """
    by_word = {}
    order = []
    for f in fixes or []:
        word, say = (f.get("word") or "").strip(), (f.get("say") or "").strip()
        if not word or not say:
            continue
        # a respelling is a spelling, not a sentence: refuse anything that would
        # turn one word into prose the engine has to guess at
        if not _WORD.match(word) or not _SAY.match(say) or len(say) > 40:
            continue
        if say.lower().strip() == word.lower().strip():
            continue
        k = word.lower()
        if k not in by_word:
            by_word[k] = (word, [])
            order.append(k)
        if say not in [s for s in by_word[k][1]]:
            by_word[k][1].append(say)
    out, clash = [], []
    for k in order:
        word, says = by_word[k]
        if len(says) > 1:
            clash.append(word)
            continue
        if len(out) >= MAX_RULES:
            break
        out.append(pron._rule(word, says[0], book=book_id))
    return out, clash


def ensure(conn, storage, uid, book_id, idxs, plugins_root=None, run=None,
           job_id="", force=False):
    """Run this book's pass, if it was asked for and has not run yet.

    Returns no rules: the model's answers are suggestions until the reader says
    otherwise, so a render never speaks them. Never raises: a model box that is
    asleep must not fail somebody's render, so a failure is reported and the
    render continues with the user's own rules.
    """
    if not enabled_for(conn, uid, book_id):
        return [], ""
    if not force and state(conn, book_id) is not None:
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
    rules, clash = _rules(book_id, fixes)
    if failed and not rules:
        # Nothing at all came back: say so and leave no row behind, so the next
        # render tries again instead of finding an empty pass and giving up.
        return [], "%d of %d part(s) failed (%s)" % (failed, total, last)
    notes = []
    if failed:
        notes.append("%d of %d part(s) failed (%s); saved what came back"
                     % (failed, total, last))
    if clash:
        # A silent drop here looks exactly like a book with nothing to decide, so
        # the reader is told which words were too ambiguous to fix.
        notes.append("%d two-reading word(s) differ inside this book and were "
                     "left plain: %s" % (len(clash), ", ".join(sorted(clash)[:8])))
    # Default-dismissed: the answers are kept so the reader can look at them, but
    # nothing they say reaches the audio until somebody taps "use these".
    _save(conn, uid, book_id, "suggested", rules, "; ".join(notes), job_id)
    return [], "; ".join(notes)


def scan(conn, storage, uid, book_id, idxs=None, job_id="", plugins_root=None,
         run=None):
    """Run the pass now, on this book only, whatever it ran before.

    This is the reader's own button: "check this book again". A pass that keeps
    its old row leaves the old answers in place, so a re-scan of a book with
    nothing new still shows the same suggestions rather than an empty screen.
    """
    if idxs is None:
        rows = conn.execute("SELECT idx FROM chapters WHERE book_id=?"
                            " ORDER BY idx", (book_id,)).fetchall()
        idxs = [r["idx"] for r in rows]
    rules, note = ensure(conn, storage, uid, book_id, idxs,
                         plugins_root=plugins_root, run=run, job_id=job_id,
                         force=True)
    return state(conn, book_id), note
