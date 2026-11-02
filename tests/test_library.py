"""Library: books + chapters persist, text lives behind storage refs."""

import tempfile
import unittest

from host.contracts import Book, Chapter
from host import library
from host.db import connect
from storage.local import LocalBackend


def _book(title="Fire", texts=("one", "two", "three")):
    return Book(title=title, chapters=[Chapter("Ch %d" % i, t)
                                       for i, t in enumerate(texts)])


class TestLibrary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fireside-lib-")
        self.storage = LocalBackend(self.tmp + "/blobs")
        self.conn = connect(":memory:")

    def test_add_and_list(self):
        library.create_user(self.conn, "u1")
        bid = library.add_book(self.conn, self.storage, "u1", _book())
        books = library.list_books(self.conn, "u1")
        self.assertEqual(len(books), 1)
        self.assertEqual(books[0]["chapters"], 3)
        self.assertEqual(books[0]["rendered"], 0)
        self.assertEqual(library.get_book(self.conn, bid)["title"], "Fire")

    def test_chapter_text_roundtrips_through_storage(self):
        library.create_user(self.conn, "u1")
        bid = library.add_book(self.conn, self.storage, "u1", _book())
        self.assertEqual(library.chapter_text(self.conn, self.storage, bid, 1), "two")

    def test_same_text_shares_one_blob(self):
        library.create_user(self.conn, "u1")
        a = library.add_book(self.conn, self.storage, "u1", _book(texts=("dup", "x")))
        b = library.add_book(self.conn, self.storage, "u1", _book(texts=("dup", "y")))
        ra = self.conn.execute("SELECT text_ref FROM chapters WHERE book_id=? AND idx=0",
                               (a,)).fetchone()["text_ref"]
        rb = self.conn.execute("SELECT text_ref FROM chapters WHERE book_id=? AND idx=0",
                               (b,)).fetchone()["text_ref"]
        self.assertEqual(ra, rb, "same text should be one content-addressed blob")

    def test_books_are_per_user(self):
        library.create_user(self.conn, "u1")
        library.create_user(self.conn, "u2")
        library.add_book(self.conn, self.storage, "u1", _book())
        self.assertEqual(library.list_books(self.conn, "u2"), [])

    def test_a_book_that_lost_a_chapter_put_is_not_half_written(self):
        """Storage puts happen before the row, so a failed put writes nothing.

        The blob backend is a network call per chapter. On the live MySQL the
        connection is autocommit, so the old order (row, then puts) left a book
        on the shelf with some of its chapters or none, and the import dedupe
        then counted it as present forever.
        """

        class FlakyStorage(LocalBackend):
            def __init__(self, root):
                LocalBackend.__init__(self, root)
                self.calls = 0

            def put(self, data):
                self.calls += 1
                if self.calls == 2:
                    raise IOError("blob put timed out")
                return LocalBackend.put(self, data)

        storage = FlakyStorage(self.tmp + "/flaky")
        library.create_user(self.conn, "u1")
        with self.assertRaises(IOError):
            library.add_book(self.conn, storage, "u1", _book())
        self.assertEqual(library.list_books(self.conn, "u1"), [])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) AS n FROM chapters")
                         .fetchone()["n"], 0)

    def test_a_book_with_no_chapters_is_refused(self):
        """A row with nothing in it shows nowhere and can never be read."""
        from host.contracts import Refused
        library.create_user(self.conn, "u1")
        with self.assertRaises(Refused):
            library.add_book(self.conn, self.storage, "u1", Book(title="Empty"))
        self.assertEqual(library.list_books(self.conn, "u1"), [])

    def test_an_empty_reimport_cannot_wipe_a_good_book(self):
        """Replacing keeps the id and drops chapters the reader no longer
        returns. A reader that returns nothing would therefore delete the
        whole book, so that case is refused instead."""
        from host.contracts import Refused
        library.create_user(self.conn, "u1")
        bid = library.add_book(self.conn, self.storage, "u1", _book())
        with self.assertRaises(Refused):
            library.replace_book(self.conn, self.storage, bid, Book(title="Fire"))
        self.assertEqual(len(library.chapters(self.conn, bid)), 3)

    def test_rendition_by_voice_can_name_the_speed(self):
        """Two rows for one voice at different speeds are different audio.

        The player already knows which row it showed the user, speed included,
        so asking for a voice without a speed used to be able to hand back the
        other row's blob - a different length, and a player that checks what it
        got would refuse to play it.
        """
        library.create_user(self.conn, "u1")
        bid = library.add_book(self.conn, self.storage, "u1", _book())
        for speed, tag in ((1.0, "normal"), (1.1, "fast")):
            library.add_rendition(self.conn, bid, 0, "fake", "v", "", speed,
                                  "ref-" + tag, 100, voice_label="V")
        self.assertEqual(library.rendition(self.conn, bid, 0, "fake", "v")["audio_ref"],
                         "ref-normal")
        self.assertEqual(
            library.rendition(self.conn, bid, 0, "fake", "v", speed=1.1)["audio_ref"],
            "ref-fast")
        self.assertEqual(
            library.rendition(self.conn, bid, 0, "fake", "v", speed="1.0")["audio_ref"],
            "ref-normal")


if __name__ == "__main__":
    unittest.main()


class TestShelfPayload(unittest.TestCase):
    """The shelf is JSON that leaves the process.

    MySQL hands back SUM() as Decimal and sqlite as int, and a Decimal in the
    payload is not a slow page: json.dumps raises, the handler dies and the
    shelf answers a connection reset. That is how this shipped once.
    """

    def test_a_decimal_count_is_still_json(self):
        import json
        from decimal import Decimal
        tmp = tempfile.mkdtemp(prefix="fireside-lib-")
        storage = LocalBackend(tmp + "/blobs")
        conn = connect(":memory:")
        library.create_user(conn, "u1")
        library.add_book(conn, storage, "u1", _book())

        class DecimalRows:
            """A connection whose SUM column looks like MySQL's: a Decimal."""
            def __init__(self, real):
                self.real = real

            def execute(self, sql, args=()):
                rows = self.real.execute(sql, args)
                if "SUM(" in sql:
                    rows = [dict(r, done=Decimal(str(r["done"] or 0)),
                                 n=Decimal(str(r["n"] or 0))) for r in rows]
                return rows

        shelf = library.list_books(DecimalRows(conn), "u1")
        json.dumps(shelf)          # the page's body: raising here is a reset
        self.assertIsInstance(shelf[0]["rendered"], int)
        self.assertIsInstance(shelf[0]["chapters"], int)
