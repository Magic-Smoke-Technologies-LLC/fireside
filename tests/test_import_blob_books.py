"""The blob-folder import: files that are stored become books that are shown.

The 2026-11-01 batch (1,828 EPUBs off a family PC into Azure Blob) sat there
invisible, because storing a file and having a shelf row are different acts.
These tests pin the three things that make the difference real: the folder is
walked completely, the file's own metadata lands in queryable columns, and a
second run does not double the shelf.
"""

import io
import json
import os
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from host import db, library  # noqa: E402
from tools import import_blob_books as imp  # noqa: E402


def _epub(title, author="A Writer", subjects=(), series=None, index=""):
    opf_meta = ['<dc:title xmlns:dc="http://purl.org/dc/elements/1.1/">%s</dc:title>'
                % title,
                '<dc:creator xmlns:dc="http://purl.org/dc/elements/1.1/">%s</dc:creator>'
                % author]
    for s in subjects:
        opf_meta.append('<dc:subject xmlns:dc="http://purl.org/dc/elements/1.1/">%s</dc:subject>' % s)
    if series:
        opf_meta.append('<meta name="calibre:series" content="%s"/>' % series)
        opf_meta.append('<meta name="calibre:series_index" content="%s"/>' % index)
    opf = ('<?xml version="1.0"?><package xmlns:dc="http://purl.org/dc/elements/1.1/">'
           '<metadata>%s</metadata><manifest>'
           '<item id="c" href="c.xhtml" media-type="application/xhtml+xml"/>'
           '</manifest><spine><itemref idref="c"/></spine></package>'
           % "".join(opf_meta))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("META-INF/container.xml",
                   '<container><rootfiles><rootfile full-path="content.opf"/>'
                   '</rootfiles></container>')
        z.writestr("content.opf", opf)
        z.writestr("c.xhtml", "<html><body><h1>One</h1><p>%s</p></body></html>"
                   % ("words here " * 30))
    return buf.getvalue()


class FakeStorage:
    """The two calls the import makes on a real backend, over a dict."""

    id = "fake"
    configured = True

    def __init__(self, blobs):
        self.blobs = dict(blobs)
        self.puts = 0

    def list_keys(self, prefix=""):
        return [{"key": k, "bytes": len(v)} for k, v in sorted(self.blobs.items())
                if k.startswith(prefix)]

    def get_key(self, key):
        if key not in self.blobs:
            raise FileNotFoundError(key)
        return self.blobs[key]

    def put(self, data):
        self.puts += 1
        import hashlib
        return hashlib.sha256(data).hexdigest()

    def has(self, ref):
        return True

    def get(self, ref):
        raise KeyError(ref)

    def delete(self, ref):
        pass


class TestImportBlobBooks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fs-import-")
        self._env = {k: os.environ.get(k) for k in
                     ("FIRESIDE_DB_TYPE", "FIRESIDE_DB_CONN", "FIRESIDE_DB",
                      "FIRESIDE_DATA")}
        os.environ["FIRESIDE_DB_TYPE"] = "sqlite"
        os.environ["FIRESIDE_DB_CONN"] = os.path.join(self.tmp, "db.sqlite")
        os.environ.pop("FIRESIDE_DB", None)
        os.environ["FIRESIDE_DATA"] = self.tmp
        self.storage = FakeStorage({
            "books/Sherry Thomas/A Study in Scarlet Women.epub":
                _epub("A Study in Scarlet Women", "Sherry Thomas",
                      ["Mystery", "Historical Fiction"], "Lady Sherlock", "1"),
            "books/Comics/Some Comic.epub": _epub("Some Comic", "An Artist"),
            "books/notes.txt": b"not a book",
        })
        self._real_storage = imp._storage
        imp._storage = lambda: self.storage
        self.db = db.connect()
        db.migrate(self.db)
        library.create_user(self.db, "u1")

    def tearDown(self):
        imp._storage = self._real_storage
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _run(self, *argv):
        old = sys.argv
        sys.argv = ["import_blob_books.py", "--user", "u1", "--prefix", "books/"] + list(argv)
        try:
            return imp.main()
        finally:
            sys.argv = old
            self.db = db.connect()

    def test_folder_becomes_shelf_rows(self):
        self.assertEqual(self._run(), 0)
        books = {b["title"]: b for b in library.list_books(self.db, "u1")}
        self.assertEqual(set(books), {"A Study in Scarlet Women", "Some Comic"})
        self.assertEqual(books["A Study in Scarlet Women"]["author"], "Sherry Thomas")
        self.assertEqual(books["A Study in Scarlet Women"]["chapters"], 1)
        # the file's own subjects arrive as tags that say where they came from
        tags = {t["tag"]: t["source"] for t in books["A Study in Scarlet Women"]["tags"]}
        self.assertEqual(tags, {"mystery": "opf", "historical fiction": "opf"})

    def test_the_files_series_lands_in_columns_the_shelf_reads(self):
        self._run()
        row = self.db.execute(
            "SELECT series_name,series_index FROM books WHERE title=?",
            ("A Study in Scarlet Women",)).fetchone()
        self.assertEqual(row["series_name"], "Lady Sherlock")
        self.assertEqual(row["series_index"], "1")

    def test_second_run_does_not_double_the_shelf(self):
        self._run()
        before = len(library.list_books(self.db, "u1"))
        self._run()
        self.assertEqual(len(library.list_books(self.db, "u1")), before)

    def test_a_file_already_imported_by_hand_is_not_imported_again(self):
        """Same book by content, not by path: an EPUB this account already owns
        (imported through /import, no blob key on it) is not a second copy."""
        from host.contracts import to_book
        data = self.storage.blobs["books/Comics/Some Comic.epub"]
        plugin = imp._reader()
        book = to_book(plugin.read({"kind": "upload", "filename": "x.epub",
                                    "data": data}, None))
        library.add_book(self.db, self.storage, "u1", book)
        self._run()
        titles = [b["title"] for b in library.list_books(self.db, "u1")]
        self.assertEqual(titles.count("Some Comic"), 1)

    def test_a_chapterless_row_does_not_hide_the_file_that_heals_it(self):
        """Run 2026-11-02 left one book on the shelf with no chapters at all:
        the row was written, then the chapter puts failed. Because the import
        dedupes on identity, every later run skipped that file and the empty
        book could never become readable. A row with nothing in it is not a
        book on the shelf; the file it came from is still owed."""
        from host.contracts import to_book
        data = self.storage.blobs["books/Comics/Some Comic.epub"]
        parsed = to_book(imp._reader().read(
            {"kind": "upload", "filename": "Some Comic.epub", "data": data}, None))
        meta = dict(parsed.meta or {})
        meta["blob"] = "books/Comics/Some Comic.epub"
        self.db.execute(
            "INSERT INTO books(id,user_id,title,source_json,author,created_at)"
            " VALUES(?,?,?,?,?,?)",
            ("stub0001", "u1", "Some Comic", json.dumps(meta), "An Artist",
             db.now()))
        self.db.commit()
        self._run()
        books = [b for b in library.list_books(self.db, "u1")
                 if b["title"] == "Some Comic"]
        self.assertEqual(len(books), 1, "one copy, not a stub beside a real book")
        self.assertEqual(books[0]["chapters"], 1)

    def test_progress_is_written_where_a_human_can_read_it(self):
        self._run()
        row = self.db.execute("SELECT value FROM settings WHERE `key`=?",
                              ("import:books/",)).fetchone()
        state = json.loads(row["value"])
        self.assertTrue(state["finished"])
        self.assertEqual(state["done"], 2)

    def test_list_shows_the_folder_without_writing(self):
        self._run("--list")
        self.assertEqual(library.list_books(self.db, "u1"), [])

    def test_one_broken_file_does_not_stop_the_batch(self):
        self.storage.blobs["books/Broken.epub"] = b"PK\x03\x04garbage"
        self._run()
        self.assertEqual(len(library.list_books(self.db, "u1")), 2)
        state = json.loads(self.db.execute(
            "SELECT value FROM settings WHERE `key`=?", ("import:books/",)
        ).fetchone()["value"])
        self.assertEqual(state["broken"], 1)
        self.assertIn("books/Broken.epub", state["failed"])


if __name__ == "__main__":
    unittest.main()
