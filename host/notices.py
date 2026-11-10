"""Notifications: something happened while you were looking somewhere else.

A conversion runs for minutes on the server. The reader closes the tab, or walks
to another screen, and the only thing that used to hold the outcome was the
page's own memory: the bell list lived in a JS array, so a failed render became
a fact nobody could ever learn. This module is the durable half. One row is one
event, seen once, kept until it is read.

The row IS the in-app channel. Any further channel (email through host/mailer,
a push later) hangs off this same call site and is delivered from notify(), not
from every one of the callers, so an event does not grow a second path just
because a second channel exists.
"""

import uuid

from host import db

KINDS = ("job", "billing", "wallet", "account")
KEEP = 50          # per user; a bell is a memory, not an archive


def notify(conn, user_id, kind, text, route="", key=""):
    """Record one event for one account and return its id ("" when there is
    nobody to tell). `key` makes a retry idempotent: the same key on an unread
    row is the same event, so a resumed render does not raise the bell twice."""
    if not user_id or not (text or "").strip():
        return ""
    if kind not in KINDS:
        kind = "account"
    text = " ".join(str(text).split())[:400]
    if key:
        row = conn.execute(
            "SELECT id FROM notifications WHERE user_id=? AND dkey=?"
            " AND COALESCE(read_at,'')=''",
            (user_id, key)).fetchone()
        if row:
            return row["id"]
    nid = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO notifications (id, user_id, kind, text, route, dkey, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (nid, user_id, kind, text, route or "", key or "", db.now()))
    conn.commit()
    _prune(conn, user_id)
    return nid


def _prune(conn, user_id):
    """Drop the oldest READ rows past KEEP. Unread rows are never dropped: the
    point of a notice is that it is still waiting for you."""
    rows = conn.execute(
        "SELECT id FROM notifications WHERE user_id=? ORDER BY created_at DESC, id DESC",
        (user_id,)).fetchall()
    ids = [r["id"] for r in rows]
    for nid in ids[KEEP:]:
        conn.execute("DELETE FROM notifications WHERE id=?"
                     " AND COALESCE(read_at,'')<>''", (nid,))
    conn.commit()


def list_for(conn, user_id, limit=20, unread_only=False):
    sql = "SELECT id, kind, text, route, created_at, read_at FROM notifications WHERE user_id=?"
    args = [user_id]
    if unread_only:
        sql += " AND COALESCE(read_at,'')=''"
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    args.append(int(limit))
    out = []
    for r in conn.execute(sql, tuple(args)).fetchall():
        d = {k: r[k] for k in ("id", "kind", "text", "route", "created_at", "read_at")}
        d["unread"] = not d.get("read_at")
        out.append(d)
    return out


def unread(conn, user_id):
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM notifications WHERE user_id=?"
        " AND COALESCE(read_at,'')=''",
        (user_id,)).fetchone()
    return int((row["n"] if row else 0) or 0)


def mark_read(conn, user_id, ids=None):
    """Mark these ids (or every unread row) as read. Ids belonging to another
    account are ignored by the WHERE, not checked first: the row is scoped by
    user_id on every statement here."""
    if ids is None:
        conn.execute("UPDATE notifications SET read_at=? WHERE user_id=?"
                     " AND COALESCE(read_at,'')=''",
                     (db.now(), user_id))
        conn.commit()
        return unread(conn, user_id)
    for nid in list(ids)[:100]:
        conn.execute(
            "UPDATE notifications SET read_at=? WHERE id=? AND user_id=?"
            " AND COALESCE(read_at,'')=''",
            (db.now(), str(nid)[:64], user_id))
    conn.commit()
    return unread(conn, user_id)