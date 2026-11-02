"""Tags: a book's own words and a reader's, told apart.

The rules that matter: a reader can carry many tags at once (that is the whole
difference from folders), the file's subjects arrive marked as the file's, and
removing one of those hides it rather than deleting it so the next import
cannot put it back.
"""

import tempfile
import unittest

from host import tags, library
from host.contracts import Book, Chapter
from host.db import connect
from storage.local import LocalBackend


class TestTags(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fs-tags-")
        self.storage = LocalBackend(self.tmp + "/blobs")
        self.conn = connect(":memory:")
        library.create_user(self.conn, "u1")
        self.bid = library.add_book(
            self.conn, self.storage, "u1",
            Book(title="A Book", chapters=[Chapter("c", "text")]),
            tags_from=["Mystery", "Historical Fiction"])

    def test_the_files_subjects_arrive_as_file_tags(self):
        got = {t["tag"]: t["source"] for t in tags.for_user(self.conn, "u1")[self.bid]}
        self.assertEqual(got, {"mystery": "opf", "historical fiction": "opf"})

    def test_a_reader_can_pile_on_their_own_tags(self):
        tags.add(self.conn, "u1", self.bid, "  Bedtime Reading ")
        got = {t["tag"]: t["source"] for t in tags.for_user(self.conn, "u1")[self.bid]}
        self.assertEqual(got["bedtime reading"], "user")
        self.assertEqual(len(got), 3)

    def test_counts_name_the_chips(self):
        tags.add(self.conn, "u1", self.bid, "bedtime")
        self.assertEqual(tags.counts(self.conn, "u1"),
                         {"mystery": 1, "historical fiction": 1, "bedtime": 1})

    def test_removing_a_file_tag_hides_it_for_good(self):
        self.assertTrue(tags.remove(self.conn, "u1", self.bid, "Mystery"))
        self.assertEqual([t["tag"] for t in tags.for_user(self.conn, "u1")[self.bid]],
                         ["historical fiction"])
        # a re-import of the same file must not resurrect it
        tags.subjects_into_tags(self.conn, "u1", self.bid, ["Mystery", "Historical Fiction"])
        self.assertEqual([t["tag"] for t in tags.for_user(self.conn, "u1")[self.bid]],
                         ["historical fiction"])

    def test_typing_the_hidden_tag_back_unhides_it(self):
        tags.remove(self.conn, "u1", self.bid, "Mystery")
        tags.add(self.conn, "u1", self.bid, "Mystery")
        got = {t["tag"]: t["source"] for t in tags.for_user(self.conn, "u1")[self.bid]}
        self.assertEqual(got["mystery"], "user")

    def test_one_shelf_cannot_tag_another_shelf(self):
        library.create_user(self.conn, "u2")
        self.assertEqual(tags.add(self.conn, "u2", self.bid, "stolen"), "")
        self.assertEqual(tags.for_user(self.conn, "u2"), {})

    def test_case_and_spaces_are_never_two_chips(self):
        tags.add(self.conn, "u1", self.bid, "Romance")
        tags.add(self.conn, "u1", self.bid, "romance ")
        self.assertEqual(len([t for t in tags.for_user(self.conn, "u1")[self.bid]
                              if t["tag"] == "romance"]), 1)


if __name__ == "__main__":
    unittest.main()
