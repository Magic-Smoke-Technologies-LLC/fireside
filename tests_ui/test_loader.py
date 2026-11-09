"""The boot mark has to be there at first paint and gone once there is a screen.

The failure this guards: an overlay that never leaves, which turns a working
page into a spinner with a dead app behind it, or one that never shows, which
is what the page had before (a blank frame while the host answers).
"""

import unittest

from tests_ui.support import BrowserTest, wait_until


class TestBootMark(BrowserTest):

    def test_it_is_gone_as_soon_as_the_door_renders(self):
        self.open_site()
        self.wait_door_rendered()
        wait_until(self.page, "() => document.getElementById('bootload').hidden")
        self.assertEqual(self.page.evaluate(
            "() => document.getElementById('bootload').classList.contains('on')"), False)

    def test_it_does_not_block_the_page(self):
        self.open_site()
        self.wait_door_rendered()
        wait_until(self.page, "() => document.getElementById('bootload').hidden")
        # the overlay is fixed and full-bleed while it is up, so a stuck
        # pointer-events value would make the whole app unclickable
        self.assertEqual(self.page.evaluate(
            "() => getComputedStyle(document.getElementById('bootload')).pointerEvents"),
            "none")

    def test_signing_in_also_clears_it(self):
        self.register("boot_reader")
        wait_until(self.page, "() => document.getElementById('bootload').hidden")
        self.assertTrue(self.page.is_visible("#v-lib"))


if __name__ == "__main__":
    unittest.main()
