"""The theme a reader picks belongs to the account, not to one browser.

Two failures this guards, both of them invisible in markup:

  * a preference that lived only in localStorage, so every new machine and every
    new browser started over and the reader had to find the setting again;
  * a theme applied by a script after the page started painting, which is a
    flash of the ordinary colours for exactly the person who needs them not to
    be. The attribute has to be on the <html> element the server sends.

The value is also validated on the way in: an unknown theme name stored from an
older client would become an attribute no stylesheet can draw, i.e. a page with
no colours at all.
"""

import unittest

from host import connections, i18n
from host.db import connect


class ThemePrefsTest(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")
        self.uid = "u1"
        self.conn.execute("INSERT INTO users(id,name,created_at) VALUES(?,?,?)",
                          (self.uid, "test", "0"))
        self.conn.commit()

    def test_a_theme_round_trips(self):
        self.assertEqual(connections.get_prefs(self.conn, self.uid)["theme"], "")
        connections.set_prefs(self.conn, self.uid, theme="cvd")
        self.assertEqual(connections.get_theme(self.conn, self.uid), "cvd")
        self.assertEqual(connections.get_prefs(self.conn, self.uid)["theme"], "cvd")

    def test_follow_the_device_is_an_empty_string_not_null(self):
        connections.set_prefs(self.conn, self.uid, theme="hc")
        connections.set_prefs(self.conn, self.uid, theme="")
        self.assertEqual(connections.get_theme(self.conn, self.uid), "")

    def test_an_unknown_theme_cannot_be_stored(self):
        connections.set_prefs(self.conn, self.uid, theme="neon")
        self.assertEqual(connections.get_theme(self.conn, self.uid), "",
                         "a theme nothing can draw would be a page with no colours")

    def test_saving_a_voice_does_not_wipe_the_theme(self):
        # INSERT OR REPLACE drops every column left out of the list, which is how
        # the reading bag was lost once already.
        connections.set_prefs(self.conn, self.uid, theme="cvd")
        connections.set_prefs(self.conn, self.uid, voice="some-voice")
        self.assertEqual(connections.get_theme(self.conn, self.uid), "cvd")
        connections.set_prefs(self.conn, self.uid, reading={"mode": "center"})
        self.assertEqual(connections.get_theme(self.conn, self.uid), "cvd")

    def test_no_user_has_no_theme(self):
        self.assertEqual(connections.get_theme(self.conn, ""), "")


class ThemeRenderTest(unittest.TestCase):
    """The attribute is in the HTML the host sends, before any script runs."""

    def _page(self):
        return "<!doctype html>\n<html lang=\"en\">\n<head></head><body>x</body></html>"

    def test_a_signed_in_theme_is_on_the_html_element(self):
        page = i18n.inject(self._page(), code="en", theme="cvd")
        self.assertIn('<html lang="en" dir="ltr" data-theme="cvd">', page)
        self.assertEqual(page.count("dir="), 1, "one dir attribute, not two")

    def test_no_theme_leaves_the_element_as_it_was(self):
        page = i18n.inject(self._page(), code="en")
        self.assertIn('<html lang="en" dir="ltr">', page)
        self.assertNotIn("data-theme", page)