"""The phone layout at 375: nothing clipped, nothing pushed off the screen.

Both bugs this locks down were invisible to a markup test. The header kept a
fixed 56px height and the tab row wrapped inside it, so the second row of tabs
was painted outside the bar (which is how Plans and Account went missing on a
phone). The player bar laid its controls out in one row wider than the screen,
so the clock and the volume sat past the right edge.

The measurement is boxes in a real browser at two widths, so a future change to
either rule fails here instead of on someone's phone.
"""

import unittest

from tests_ui.support import BrowserTest, wait_visible


class TestPhoneLayout(BrowserTest):

    def boxes(self, sel):
        return self.page.eval_on_selector_all(sel, """els => els.map(e => {
            const r = e.getBoundingClientRect();
            const cs = getComputedStyle(e);
            return {id: e.id || e.dataset.v || e.dataset.p || e.tagName,
                    x: r.x, y: r.y, w: r.width, h: r.height,
                    right: r.right, bottom: r.bottom,
                    shown: cs.display !== 'none' && r.width > 0 && r.height > 0};
        })""")

    def one(self, sel):
        return self.page.eval_on_selector(sel, """e => {
            const r = e.getBoundingClientRect();
            return {x: r.x, y: r.y, w: r.width, h: r.height,
                    right: r.right, bottom: r.bottom}; }""")

    def test_header_holds_its_tabs_on_a_phone(self):
        self.register("phone_header")
        self.wait_in_app()
        self.page.set_viewport_size({"width": 375, "height": 812})
        self.page.wait_for_timeout(400)
        vw = self.page.evaluate("() => innerWidth")
        head = self.one("header")
        tabs = [b for b in self.boxes("header nav.tabs button") if b["shown"]]
        self.assertGreaterEqual(len(tabs), 2, "the nav still has its destinations")
        self.assertGreaterEqual(head["h"], 56, "the bar keeps its height as a floor")
        for b in tabs:
            self.assertLessEqual(b["right"], vw + 0.5, "%s runs off the screen" % b["id"])
            self.assertLessEqual(b["bottom"], head["bottom"] + 0.5,
                                 "%s is painted outside the header" % b["id"])
            self.assertGreaterEqual(b["x"], -0.5)
        # two rows, not one row clipped: the bar grows instead
        self.assertGreater(head["h"], 56, "wrapped tabs have to make the bar taller")

    def test_player_bar_fits_375(self):
        self.register("phone_transport")
        self.wait_in_app()
        self.page.set_viewport_size({"width": 375, "height": 812})
        self.page.wait_for_timeout(300)
        # The bar only draws while something is playing. A long book title and a
        # long clock are the widest content it ever has to hold, so they are the
        # case worth measuring; the CSS is what is under test, not the session.
        self.page.evaluate("""() => {
            const t = document.getElementById('transport');
            t.classList.add('on');
            document.getElementById('tnowbook').textContent =
                'The Long-Winded Title Of A Very Long Book · ch 12';
            document.getElementById('ttimes').textContent = '48:31 / 1:12:09';
        }""")
        self.page.wait_for_timeout(200)
        vw = self.page.evaluate("() => innerWidth")
        bar = self.one("#transport")
        self.assertLessEqual(bar["right"], vw + 0.5, "the bar itself stays on screen")
        self.assertGreaterEqual(bar["x"], -0.5)
        for b in self.boxes("#transport > *"):
            if not b["shown"]:
                continue
            self.assertLessEqual(b["right"], bar["right"] + 0.5,
                                 "%s is pushed past the bar's right edge" % b["id"])
            self.assertGreaterEqual(b["x"], bar["x"] - 0.5,
                                    "%s is pushed past the bar's left edge" % b["id"])

    def test_wide_screen_keeps_one_row(self):
        self.register("wide_header")
        self.wait_in_app()
        self.page.set_viewport_size({"width": 1280, "height": 900})
        self.page.wait_for_timeout(300)
        tabs = [b for b in self.boxes("header nav.tabs button") if b["shown"]]
        tops = {round(b["y"]) for b in tabs}
        self.assertEqual(len(tops), 1, "on a wide screen the tabs stay one row")
        self.assertLess(self.one("header")["h"], 70)

    def test_the_door_glow_follows_the_theme(self):
        self.register("door_glow")
        self.wait_in_app()
        self.page.evaluate("() => document.getElementById('signout')"
                           "&& document.getElementById('signout').click()")
        wait_visible(self.page, "#lock")
        light = self.page.evaluate("""() => {
            document.documentElement.setAttribute('data-theme', 'light');
            return getComputedStyle(document.getElementById('lock')).backgroundImage;
        }""")
        dark = self.page.evaluate("""() => {
            document.documentElement.removeAttribute('data-theme');
            return getComputedStyle(document.getElementById('lock')).backgroundImage;
        }""")
        self.assertNotEqual(light, dark, "the door paints the same room in every theme")
        self.assertNotIn("36, 28, 19", light, "the dark door hex leaked into the light theme")
        self.assertIn("240, 229, 210", light, "the light door uses the light glow")


if __name__ == "__main__":
    unittest.main()
