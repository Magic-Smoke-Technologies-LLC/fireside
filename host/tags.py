"""Tags: the shelf's own words for its books.

A tag is a short label a reader can put on any book, and every tag has a
source. Two sources exist and they are not the same promise:

  opf   the ebook's own metadata said it (dc:subject), imported with the book
  user  the reader typed it

That difference is the whole point. A derived tag can be wrong without anyone
lying: a publisher's subject list is noisy ("Fiction", "Romance", "General").
A reader's tag is a decision. So the UI shows both, and a reader removing an
opf tag removes it for good rather than having it re-derived on the next
import - the removal is remembered as a `hidden` row, not a deletion.

Auto tags ("unread", "series") are never stored. They are facts about the
library at read time, and storing them would make them go stale the moment a
chapter was voiced or a series was renamed.
"""

import uuid

MAX_TAG = 40
MAX_TAGS_PER_BOOK = 40


def clean(tag):
    """One tag, as it will be stored: collapsed whitespace, lowercase, capped.

    Lowercase because "Romance" and "romance" being two chips is a bug the
    reader would hit on their second book, not a feature.
    """
    t = " ".join(str(tag or "").split())[:MAX_TAG]
    return t.lower()


def add(conn, user_id, book_id, tag, source="user"):
    """Put one tag on one book. Returns the stored tag, or "" if it was empty."""
    t = clean(tag)
    if not t:
        return ""
    from host.db import now
    own = conn.execute("SELECT id FROM books WHERE id=? AND user_id=?",
                       (book_id, user_id)).fetchone()
    if not own:
        return ""
    n = conn.execute("SELECT COUNT(*) c FROM tags WHERE book_id=?", (book_id,)).fetchone()["c"]
    if n >= MAX_TAGS_PER_BOOK:
        return ""
    conn.execute("INSERT OR IGNORE INTO tags(book_id,user_id,tag,source,created_at)"
                 " VALUES(?,?,?,?,?)", (book_id, user_id, t, source, now()))
    # A tag the reader re-adds by hand overrides the fact that they had hidden it.
    conn.execute("DELETE FROM tag_hidden WHERE book_id=? AND tag=?", (book_id, t))
    conn.commit()
    return t


def remove(conn, user_id, book_id, tag):
    """Take one tag off one book. An opf tag is hidden, not forgotten: it comes
    from the file, so deleting the row would only make the next import put it
    back and the reader would have to remove it again."""
    t = clean(tag)
    if not t:
        return False
    row = conn.execute("SELECT source FROM tags WHERE book_id=? AND tag=? AND user_id=?",
                       (book_id, t, user_id)).fetchone()
    if not row:
        return False
    conn.execute("DELETE FROM tags WHERE book_id=? AND tag=?", (book_id, t))
    if row["source"] == "opf":
        from host.db import now
        conn.execute("INSERT OR IGNORE INTO tag_hidden(book_id,tag,user_id,created_at)"
                     " VALUES(?,?,?,?)", (book_id, t, user_id, now()))
    conn.commit()
    return True


def hidden(conn, user_id):
    return {r["book_id"] + "\x00" + r["tag"] for r in conn.execute(
        "SELECT book_id,tag FROM tag_hidden WHERE user_id=?", (user_id,))}


def for_user(conn, user_id):
    """{book_id: [{"tag":..., "source":...}]} for a whole shelf in one query."""
    out = {}
    for r in conn.execute("SELECT book_id,tag,source FROM tags WHERE user_id=?"
                          " ORDER BY tag", (user_id,)):
        out.setdefault(r["book_id"], []).append(
            {"tag": r["tag"], "source": r["source"] or "user"})
    return out


def counts(conn, user_id):
    """{tag: n} over the shelf. What the filter chips are built from."""
    out = {}
    for r in conn.execute("SELECT tag, COUNT(DISTINCT book_id) n FROM tags"
                          " WHERE user_id=? GROUP BY tag ORDER BY tag", (user_id,)):
        out[r["tag"]] = r["n"]
    return out


def set_for_book(conn, user_id, book_id, tags, source="user"):
    """Replace a book's user tags with this list (opf tags are left alone).

    Used by the import, which knows a book's subjects in one go, and by the
    settings panel, where the reader edits one field.
    """
    conn.execute("DELETE FROM tags WHERE book_id=? AND user_id=? AND source=?",
                 (book_id, user_id, source))
    conn.commit()
    seen = []
    for t in tags or []:
        got = add(conn, user_id, book_id, t, source=source)
        if got and got not in seen:
            seen.append(got)
    return seen


def subjects_into_tags(conn, user_id, book_id, subjects):
    """Apply the book file's own subject list, and only that.

    An opf tag the reader deleted must not come back on a re-import, so a
    hidden (book, tag) pair is skipped rather than re-added.
    """
    if not subjects:
        return []
    skip = hidden(conn, user_id)
    keep = [s for s in subjects
            if clean(s) and (book_id + "\x00" + clean(s)) not in skip]
    return set_for_book(conn, user_id, book_id, keep, source="opf")
