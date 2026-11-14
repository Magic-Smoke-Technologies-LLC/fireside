"""The foot: who runs this, how to reach them, and the papers.

It has to be on every page, and "every page" is the part that goes wrong quietly.
The app shell hides <header> and <main> behind the door, so a foot placed inside
<main> is invisible to the stranger who needs it most, and the door's overlay is
fixed and full-bleed, so a foot placed below it is behind it.

What is checked: the links are on the door and above it, they are on the
signed-in app screens, they actually navigate, and the two pages they point at
are reachable with no session at all.
"""

import unittest

from tests_ui.support import BrowserTest, wait_until, wait_visible


class TestFoot(BrowserTest):

    FOOT = ("/about", "/contact", "/terms", "/refunds", "/privacy")

    def _hrefs(self):
        return [a.get_attribute("href") for a in
                self.page.query_selector_all("#foot a")]

    def test_the_door_carries_the_links_and_they_are_not_behind_the_overlay(self):
        self.open_site()
        self.wait_door_rendered()
        self.assertTrue(self.page.is_visible("#foot"))
        self.assertEqual(self._hrefs(), list(self.FOOT))
        # being "visible" is not the same as being on top: a fixed overlay with
        # a higher z-index covers it while it still reports as visible.
        top = self.page.evaluate(
            "() => { const a = document.querySelector('#foot a');"
            " const r = a.getBoundingClientRect();"
            " const e = document.elementFromPoint(r.x + r.width/2, r.y + r.height/2);"
            " return !!e && !!e.closest('#foot'); }")
        self.assertTrue(top, "the foot is rendering under the door overlay")
        self.assert_no_page_errors()

    def test_a_signed_in_reader_sees_it_on_every_view(self):
        self.register("ui_foot")
        self.wait_library(0)
        self.assertTrue(self.page.is_visible("#foot"))
        self.page.click("#tabs button[data-v='set']")
        wait_until(self.page, "() => document.querySelector('#v-set').classList.contains('on')")
        self.assertTrue(self.page.is_visible("#foot"))
        self.assertEqual(self._hrefs(), list(self.FOOT))

    def test_the_link_goes_somewhere_with_an_answer(self):
        self.open_site()
        self.wait_door_rendered()
        self.page.click("#foot a[href='/about']")
        wait_until(self.page, "() => location.pathname === '/about'")
        self.assertIn("About fireside", self.page.text_content("h1"))
        # and that page carries the same foot, so you can get back out of it
        self.assertEqual(
            [a.get_attribute("href") for a in self.page.query_selector_all(".foot a")],
            list(self.FOOT))

    def test_the_pages_are_public(self):
        # A payment processor and a locked-out user both arrive with no session.
        # These are pages, not JSON, so read the bytes rather than the parser.
        import urllib.request
        for path in self.FOOT:
            with urllib.request.urlopen(self.host.url(path), timeout=30) as r:
                body = r.read().decode("utf-8")
            self.assertEqual(r.status, 200, path)
            self.assertGreater(len(body.strip()), 500, path)


if __name__ == "__main__":
    unittest.main()