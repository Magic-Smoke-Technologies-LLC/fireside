"""The chapter list has to come alive while a conversion is still running.

Cat reported it plainly: she starts a 10-chapter book converting and the
chapters she can already listen to do not appear until the last one is done.
She was right. The render worker commits each chapter as it lands, and the host
has always served those chapters, but the page only re-read the book after the
job finished, so mid-run it was painting a list that was minutes old.

This reads ui/index.html the way the host serves it, and pins the three things
that make the fix real: the refresh exists, the poll loop calls it when the job
counter moves, and it repaints without moving the reader.

On unittest.TestCase on purpose: CI runs unittest discover, which collects
TestCase classes, and a bare `def test_x()` is collected by nothing.
"""

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PAGE = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")


class TestChaptersDuringConversion(unittest.TestCase):
    def test_the_refresh_exists_and_reads_the_book_from_the_server(self):
        self.assertIn("async function refreshChaptersDuringJob(", PAGE)
        body = PAGE.split("async function refreshChaptersDuringJob(", 1)[1]
        body = body.split("\n}\n", 1)[0]
        self.assertIn("'/progress?user='", body)
        self.assertIn("book.chapters = prog.chapters", body)

    def test_the_conversion_loop_refreshes_when_the_job_counter_moves(self):
        m = re.search(
            r"const j = await api\('/job\?id=.*?;.*?if \(j\.current !== shown\) \{"
            r".*?shown = j\.current;.*?await refreshChaptersDuringJob\(book\.id\);",
            PAGE, re.S)
        self.assertTrue(m, "the poll loop does not repaint as chapters land")

    def test_the_refresh_does_not_reopen_the_book_or_move_the_reader(self):
        body = PAGE.split("async function refreshChaptersDuringJob(", 1)[1]
        body = body.split("\n}\n", 1)[0]
        # openBook resets cur to -1 and swaps the URL; a background refresh has
        # no business doing either while a person may be listening.
        self.assertNotIn("openBook(", body)
        self.assertIn("refreshStates();", body)
        self.assertIn("renderPlay();", body)

    def test_a_failed_refresh_does_not_break_the_conversion(self):
        body = PAGE.split("async function refreshChaptersDuringJob(", 1)[1]
        body = body.split("\n}\n", 1)[0]
        self.assertIn("catch (_)", body)


if __name__ == "__main__":
    unittest.main()
