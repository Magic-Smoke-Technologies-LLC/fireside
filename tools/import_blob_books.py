#!/usr/bin/env python3
"""Turn a folder of book files already in blob storage into shelf rows.

Why this exists: a library that is *stored* is not a library that is *shown*.
On 2026-11-01 1,828 EPUBs were copied off a family PC straight into Azure Blob
under `books/`, and nothing on readfireside.com changed, because a shelf row is
a database row plus parsed chapter text, not a blob. This is the missing step,
written as a resumable batch job so it can be re-run after a crash or a new
folder without importing anything twice.

It is deliberately a plain script with no HTTP in it: run it as a Container App
job (or on a laptop against a local sqlite db) and it reads the same env the app
does - FIRESIDE_DB_*, FIRESIDE_AZURE_* - through the same host modules. A
duplicate is decided by the book's own identity (ISBN when the epub carries
one, else the file bytes), which is the same rule /import uses, so an EPUB that
was already imported by hand is skipped rather than doubled.

Usage:
    python3 tools/import_blob_books.py --user local:2forkittykat --prefix books/
    python3 tools/import_blob_books.py --list            # what is in the folder
    python3 tools/import_blob_books.py --dry-run --limit 20

Progress is written to the `settings` table under `import:<prefix>` on every
book, so a run in progress can be watched from a SQL client instead of a log
nobody has.
"""

import argparse
import json
import os
import sys
import time
import traceback

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from host import db, library  # noqa: E402
from host.contracts import Refused, to_book  # noqa: E402
from host.pluginhost import discover  # noqa: E402


def _reader():
    """The epub reader, discovered the same way the app discovers it."""
    for p in discover("reader", os.path.join(_ROOT, "plugins")):
        if p.id == "epub":
            return p.obj
    raise SystemExit("no epub reader plugin found")


def _storage():
    for p in discover("storage", os.path.join(_ROOT, "plugins")).values():
        obj = p.obj
        if getattr(obj, "configured", True) and hasattr(obj, "list_keys"):
            return obj
    raise SystemExit("no storage plugin with list_keys (azure only)")


def _status_set(conn, key, value):
    conn.execute(
        "INSERT INTO settings(`key`,value) VALUES(?,?)"
        " ON CONFLICT(`key`) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value)))
    conn.commit()


def _status_get(conn, key):
    r = conn.execute("SELECT value FROM settings WHERE `key`=?", (key,)).fetchone()
    try:
        return json.loads(r["value"]) if r else {}
    except (TypeError, ValueError):
        return {}


def _known_identities(conn, user):
    """Every identity already on this shelf, and every blob key claimed.

    One pass over the source_json of the user's books. The identity is what the
    import dedupes on; the blob key is what makes a resumed run cheap, because
    a book that came from blob knows which blob it came from.
    """
    ids, keys = set(), set()
    for r in conn.execute("SELECT source_json FROM books WHERE user_id=?", (user,)):
        try:
            meta = json.loads(r["source_json"] or "{}")
        except ValueError:
            continue
        if not isinstance(meta, dict):
            continue
        if meta.get("identity"):
            ids.add(meta["identity"])
        if meta.get("blob"):
            keys.add(meta["blob"])
    return ids, keys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="", help="the account whose shelf to fill")
    ap.add_argument("--prefix", default="books/", help="blob prefix to walk")
    ap.add_argument("--limit", type=int, default=0, help="stop after N new books")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list", action="store_true", help="print the folder and exit")
    ap.add_argument("--retry-failed", action="store_true",
                    help="try files that this prefix's last run could not read")
    a = ap.parse_args()

    storage = _storage()
    conn = db.connect()
    db.migrate(conn)

    blobs = [b for b in storage.list_keys(a.prefix)
             if b["key"].lower().endswith(".epub")]
    blobs.sort(key=lambda b: b["key"])
    if a.list:
        for b in blobs:
            print("%10d  %s" % (b["bytes"], b["key"]))
        print("%d epub(s) under %s" % (len(blobs), a.prefix))
        return 0
    if not a.user:
        raise SystemExit("--user is required (the account whose shelf to fill)")

    state_key = "import:" + a.prefix
    state = _status_get(conn, state_key)
    failed = {} if a.retry_failed else dict(state.get("failed") or {})
    have_ids, have_keys = _known_identities(conn, a.user)
    reader = _reader()

    todo = [b for b in blobs if b["key"] not in have_keys
            and b["key"] not in failed]
    if a.limit:
        todo = todo[:a.limit]
    print(json.dumps({"folder": len(blobs), "already": len(blobs) - len(todo),
                      "todo": len(todo), "user": a.user}), flush=True)
    if a.dry_run:
        for b in todo[:20]:
            print("  would import", b["key"])
        return 0

    started = time.time()
    done = skipped = broken = 0
    chars = 0
    for i, b in enumerate(todo, 1):
        key = b["key"]
        try:
            data = storage.get_key(key)
            parsed = to_book(reader.read(
                {"kind": "upload", "filename": key.rsplit("/", 1)[-1], "data": data},
                fetch=None))
            ident = (parsed.meta or {}).get("identity") or ""
            if ident and ident in have_ids:
                skipped += 1
                have_keys.add(key)
            else:
                meta = dict(parsed.meta or {})
                meta["blob"] = key
                # Where it came from, for a reader who wonders why a book with
                # no cover has a folder name in its history.
                meta.setdefault("source", "epub")
                parsed.meta = meta
                bid = library.add_book(conn, storage, a.user, parsed,
                                       tags_from=(meta.get("subjects") or []))
                # The file's own series claim rides on the columns the shelf
                # already reads, so a calibre series groups without the import
                # inventing its own container.
                if meta.get("series"):
                    library.set_details(conn, bid, series_name=meta["series"][:255],
                                        series_index=str(meta.get("series_index") or "")[:32])
                if meta.get("publisher") or meta.get("published"):
                    library.set_details(conn, bid,
                                        publisher=(meta.get("publisher") or "")[:255],
                                        published=(meta.get("published") or "")[:64])
                have_keys.add(key)
                if ident:
                    have_ids.add(ident)
                done += 1
                chars += sum(len(c.text or "") for c in parsed.chapters)
        except (Refused, FileNotFoundError) as e:
            broken += 1
            failed[key] = "%s: %s" % (type(e).__name__, getattr(e, "detail", "") or e)
        except Exception as e:                       # one bad file never stops a batch
            broken += 1
            failed[key] = "%s: %s" % (type(e).__name__, str(e)[:200])
            if os.environ.get("IMPORT_VERBOSE"):
                traceback.print_exc()
        if i % 10 == 0 or i == len(todo):
            _status_set(conn, state_key, {
                "user": a.user, "folder": len(blobs), "done": done,
                "skipped": skipped, "broken": broken, "todo": len(todo),
                "chars": chars, "elapsed_s": round(time.time() - started, 1),
                "failed": dict(list(failed.items())[:200]),
                "updated_at": db.now(), "finished": i == len(todo)})
            print(json.dumps({"i": i, "of": len(todo), "done": done,
                              "skipped": skipped, "broken": broken}), flush=True)

    _status_set(conn, state_key, {
        "user": a.user, "folder": len(blobs), "done": done, "skipped": skipped,
        "broken": broken, "todo": len(todo), "chars": chars,
        "elapsed_s": round(time.time() - started, 1),
        "failed": dict(list(failed.items())[:200]),
        "updated_at": db.now(), "finished": True})
    print(json.dumps({"done": done, "skipped": skipped, "broken": broken,
                      "chars": chars, "seconds": round(time.time() - started, 1)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
