"""The door's fire is decoration with a budget.

It sits behind the welcome pane and nowhere else, it is not requested at all
when the build has no clip, and a phone does not pay for it. Each of those is
invisible from a markup test: the element exists in all three cases.

The clip itself is licensed media the owner supplies, so this suite runs
against the build as it ships today, with no file. The "clip present" state is
covered server-side in tests/test_hearth.py.
"""

import unittest

from tests_ui.support import BrowserTest, wait_until, wait_visible


class TestDoorHearth(BrowserTest):

    def test_the_build_without_a_clip_asks_for_nothing(self):
        self.open_site()
        self.wait_door_rendered()
        # not one request for a file this build does not ship: a 404 on the
        # first screen is a console error a stranger can see
        asked = [u for (s, u) in self.refused if "hearth" in u]
        self.assertEqual(asked, [], "the page requested a clip this build has no file for")
        shown = self.page.evaluate(
            "() => { const v = document.getElementById('hearthvid');"
            " return {src: v.getAttribute('src'), cls:"
            " document.getElementById('hearth').className}; }")
        self.assertIn(shown["src"], (None, ""))
        self.assertNotIn("on", shown["cls"].split())

    def test_the_fire_never_sits_behind_the_form(self):
        self.open_site()
        self.wait_door_rendered()
        # it belongs to the pane that says what this is, not to the one that
        # takes a password: moving pixels under a field is how people mistype
        self.assertIsNone(self.page.query_selector("#lockform #hearth"))
        self.assertIsNotNone(self.page.query_selector("#lockwel #hearth"))
        fire = self.page.evaluate(
            "() => { const r = document.getElementById('hearth').getBoundingClientRect();"
            " return {x: r.x, y: r.y, w: r.width, h: r.height}; }")
        card = self.page.query_selector("#lockform").bounding_box()
        self.assertLess(fire["x"] + fire["w"], card["x"] + 1,
                        "the fire's right edge has to stop at the form")

    def test_a_phone_does_not_pay_for_the_fire(self):
        self.page.set_viewport_size({"width": 360, "height": 640})
        self.open_site()
        self.wait_door_rendered()
        display = self.page.evaluate(
            "() => getComputedStyle(document.getElementById('hearth')).display")
        self.assertEqual(display, "none")

    def test_the_hearth_is_not_in_the_accessibility_tree(self):
        self.open_site()
        self.wait_door_rendered()
        self.assertEqual(
            self.page.evaluate(
                "() => document.getElementById('hearth').getAttribute('aria-hidden')"),
            "true")
        # and it cannot be tabbed to or tapped
        self.assertEqual(
            self.page.evaluate("""() => {
                const v = document.getElementById('hearthvid');
                return getComputedStyle(document.getElementById('hearth')).pointerEvents;
            }"""), "none")


if __name__ == "__main__":
    unittest.main()