"""Author keys and the read mark: two shelf facts that used to be missing.

The key is comparison only; the spelling stays. The read mark is per user.
"""

import tempfile
import unittest

from host.authors import author_key
from host import pron
from host.contracts import Book, Chapter
from host import library
from host.db import connect
from storage.local import LocalBackend


def _book(title="Seed", author=""):
    return Book(title=title, chapters=[Chapter("Ch", "text of " + title)],
                meta={"author": author} if author else {})


class TestAuthorKey(unittest.TestCase):
    def test_initials_do_not_make_a_second_author(self):
        self.assertEqual(author_key("B. Arthur"), author_key("B Arthur"))

    def test_last_first_is_the_same_person(self):
        self.assertEqual(author_key("Arthur, B."), author_key("B Arthur"))

    def test_spaced_initials_fold(self):
        self.assertEqual(author_key("J. R. R. Tolkien"),
                         author_key("J.R.R. Tolkien"))

    def test_accents_fold(self):
        self.assertEqual(author_key("Brontë"), author_key("Bronte"))

    def test_ampersand_is_the_word(self):
        self.assertEqual(author_key("Miller & Sons"),
                         author_key("Miller and Sons"))

    def test_whitespace_and_case(self):
        self.assertEqual(author_key("  ursula   K Le Guin "),
                         author_key("Ursula K. Le Guin"))

    def test_two_commas_are_left_alone(self):
        # no guessing at suffixes; the key is still stable, which is the point
        self.assertEqual(author_key("Smith, John, Jr"),
                         author_key("smith,  john,  jr"))

    def test_empty(self):
        for v in ("", "   ", None, ". ."):
            self.assertEqual(author_key(v), "")

    def test_different_people_stay_apart(self):
        self.assertNotEqual(author_key("B. Arthur"), author_key("A. Arthur"))
        self.assertNotEqual(author_key("Ann Leckie"), author_key("Ann Le Guin"))


class TestAuthorScopedRules(unittest.TestCase):
    """A pronunciation rule typed on one spelling of an author has to reach the
    books that carry the other spelling."""

    def test_rule_reaches_the_other_spelling(self):
        rules = [{"from": "\u029b", "to": "coin", "book": "", "author": "B. Arthur"}]
        self.assertEqual([r["to"] for r in pron.for_book(rules, "b9", "B Arthur")], ["coin"])

    def test_rule_stays_out_of_a_different_author(self):
        rules = [{"from": "\u029b", "to": "coin", "book": "", "author": "B. Arthur"}]
        self.assertEqual(pron.for_book(rules, "b9", "B Arthur Jr"), [])


class TestShelfFacts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fireside-authors-")
        self.storage = LocalBackend(self.tmp + "/blobs")
        self.conn = connect(":memory:")
        library.create_user(self.conn, "u1")
        library.create_user(self.conn, "u2")

    def add(self, title, author="", user="u1"):
        return library.add_book(self.conn, self.storage, user,
                                _book(title, author))

    def test_listing_carries_the_key_and_the_spelling(self):
        self.add("One", "B. Arthur")
        row = library.list_books(self.conn, "u1")[0]
        self.assertEqual(row["author"], "B. Arthur")      # display untouched
        self.assertEqual(row["author_key"], "b arthur")

    def test_two_spellings_share_a_key_but_keep_their_own(self):
        self.add("One", "B. Arthur")
        self.add("Two", "B Arthur")
        rows = library.list_books(self.conn, "u1")
        keys = {r["author_key"] for r in rows}
        names = {r["author"] for r in rows}
        self.assertEqual(keys, {"b arthur"})
        self.assertEqual(names, {"B. Arthur", "B Arthur"})

    def test_new_books_are_unread(self):
        self.add("One")
        self.assertFalse(library.list_books(self.conn, "u1")[0]["read"])

    def test_mark_and_clear(self):
        bid = self.add("One")
        library.set_read(self.conn, bid, True)
        self.assertTrue(library.list_books(self.conn, "u1")[0]["read"])
        self.assertTrue(library.is_read(self.conn, bid))
        library.set_read(self.conn, bid, False)
        self.assertFalse(library.list_books(self.conn, "u1")[0]["read"])
        self.assertFalse(library.is_read(self.conn, bid))

    def test_the_mark_is_per_user(self):
        bid = self.add("One", user="u1")
        library.set_read(self.conn, bid, True)
        self.assertEqual(len(library.list_books(self.conn, "u2")), 0)

    def test_an_old_row_without_the_column_reads_unread(self):
        # the migration adds read_at with ''; a row inserted explicitly as ''
        # is the same shape a pre-migration row lands in.
        bid = self.add("One")
        self.conn.execute("UPDATE books SET read_at='' WHERE id=?", (bid,))
        self.conn.commit()
        self.assertFalse(library.list_books(self.conn, "u1")[0]["read"])


if __name__ == "__main__":
    unittest.main()
