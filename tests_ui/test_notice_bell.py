"""The bell survives the tab.

A notice is only worth writing if it is still there when the reader comes back,
and that is a claim about the page, not about the table: the row exists, the
badge has to light from it, the menu has to show it, and opening it has to mark
it read and land the reader on the thing it is about. The row is planted
directly in the host's own database, because the event that writes one takes a
whole conversion and this is not a test of the conversion.
"""

import os
import unittest

from tests_ui.support import BrowserTest, wait_visible


class TestNoticeBell(BrowserTest):

    def _db(self):
        from host.db import connect
        return connect(os.path.join(self.host.data, "fireside.db"))

    def _only_user(self, conn):
        return conn.execute("SELECT id FROM users LIMIT 1").fetchone()["id"]

    def _plant(self, text, route=""):
        from host import notices
        conn = self._db()
        try:
            return notices.notify(conn, self._only_user(conn), "job", text, route=route)
        finally:
            conn.close()

    def _unread(self):
        from host import notices
        conn = self._db()
        try:
            return notices.unread(conn, self._only_user(conn))
        finally:
            conn.close()

    def test_the_badge_lights_from_a_row_the_tab_never_saw(self):
        self.register("bell_geo")
        self.wait_in_app()
        nid = self._plant("Moby Dick is ready to play.", route="#/play/nowhere")
        self.assertEqual(self._unread(), 1)
        self.assertNotIn("alert", self.page.get_attribute("#bell", "class") or "")
        # coming back to the tab is the case the row was kept for
        self.page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
        self.page.wait_for_timeout(800)
        self.assertIn("alert", self.page.get_attribute("#bell", "class") or "",
                      "a stored notice has to raise the badge")
        self.page.click("#bell")
        wait_visible(self.page, "#alertmenu button")
        self.assertIn("Moby Dick is ready to play.",
                      self.page.text_content("#alertmenu"))
        # opening it marks it read on the server, not only in the page
        self.page.click("#alertmenu button:has-text('Moby Dick')")
        self.page.wait_for_timeout(600)
        self.assertEqual(self._unread(), 0, "opening a notice reads it")
        self.assertNotIn("alert", self.page.get_attribute("#bell", "class") or "")
        self.assert_no_page_errors()


if __name__ == "__main__":
    unittest.main()