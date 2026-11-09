"""Colour-vision safe has to be reachable before there is an account, and it has
to follow the account once there is one.

Three claims, each of them something a passing markup test would miss:

  * a stranger can pick a theme on the door and see it applied immediately,
    without reloading and without signing up;
  * that choice becomes the account's, so the next device starts right instead
    of starting over;
  * the account's theme is on the html element in the HTML the host sends, not
    applied by a script afterwards. Applied after paint it is a flash of the
    ordinary colours for the one reader who cannot use them.
"""

import unittest

from tests_ui.support import BrowserTest, cookie_jar, wait_until, wait_visible


class TestThemeOnTheDoor(BrowserTest):

    def _picked(self):
        return self.page.evaluate(
            "() => document.documentElement.getAttribute('data-theme')")

    def _choose_on_door(self, theme):
        """The door carries the same gear as the bar: open it and pick."""
        self.page.click("#thememountdoor .themebtn")
        wait_visible(self.page, "#thememountdoor .thememenu")
        self.page.click("#thememountdoor .thememenu button[data-theme='%s']" % theme)
        wait_until(self.page, "() => true")

    def test_a_stranger_can_choose_before_an_account(self):
        self.open_site()
        self.wait_door_rendered()
        wait_visible(self.page, "#thememountdoor .themebtn")
        self._choose_on_door("cvd")
        # no reload: the attribute is the theme
        self.assertEqual(self._picked(), "cvd")
        self.assertEqual(self.page.evaluate("() => localStorage.getItem('fs.theme')"),
                         "cvd")

    def test_the_door_choice_does_not_need_the_settings_page(self):
        # the settings control is on the signed-in side; a stranger has no route
        # to it, which is the reason the door carries its own.
        self.open_site()
        self.wait_door_rendered()
        self.assertTrue(self.page.is_visible("#thememountdoor .themebtn"))
        self.assert_no_page_errors()

    def test_signing_up_keeps_the_choice_and_makes_it_the_accounts(self):
        self.open_site()
        self.wait_door_rendered()
        self._choose_on_door("cvd")
        self.page.fill("#lockuser", "cvd_reader")
        self.page.fill("#lockemail", "cvd_reader@example.test")
        self.page.fill("#lockpass", "bench-correction-1")
        self.page.fill("#lockpass2", "bench-correction-1")
        self.page.click("#lockgo")
        self.wait_in_app()
        jar = cookie_jar(self.context)
        wait_until(self.page, "() => true")          # let the /prefs POST land
        status, me = self.host.request("/me", cookies=jar)
        self.assertEqual(status, 200)
        self.assertEqual(me.get("theme"), "cvd",
                         "the door choice has to become the account's, or a new "
                         "browser starts over")

    def _served(self, cookies=None):
        import urllib.request
        headers = {}
        if cookies:
            headers["Cookie"] = "; ".join("%s=%s" % (k, v) for k, v in cookies.items())
        req = urllib.request.Request(self.host.url("/"), headers=headers)
        with urllib.request.urlopen(req) as r:
            return r.read().decode()

    def test_the_account_theme_is_in_the_bytes_the_host_sends(self):
        self.register("cvd_second")
        jar = cookie_jar(self.context)
        status, _ = self.host.request("/prefs", cookies=jar, method="POST",
                                      body={"theme": "hc"})
        self.assertEqual(status, 200)
        # read the served bytes, do not ask the browser: by the time the browser
        # answers, a script could have set the attribute and hidden the flash
        self.assertIn('data-theme="hc"', self._served(jar),
                      "the account theme belongs on the html element the host "
                      "sends, not in a script that runs after paint")
        # and a stranger's html element carries no theme attribute, so the CSS
        # media queries decide (the stylesheet itself mentions data-theme, which
        # is why this reads the tag and not the page)
        import re
        tag = re.search(r"<html[^>]*>", self._served()).group(0)
        self.assertNotIn("data-theme", tag)

    def test_a_new_browser_lands_in_the_account_theme(self):
        self.register("cvd_third")
        jar = cookie_jar(self.context)
        self.host.request("/prefs", cookies=jar, method="POST", body={"theme": "cvd"})
        # a fresh context: no localStorage, i.e. a machine that has never seen
        # this reader before
        other = self.browser.new_context(viewport={"width": 1280, "height": 900})
        try:
            other.add_cookies([{"name": k, "value": v, "url": self.host.url("/")}
                               for k, v in jar.items()])
            page = other.new_page()
            page.goto(self.host.url("/"))
            wait_until(page, "() => document.readyState === 'complete'")
            self.assertEqual(
                page.evaluate("() => document.documentElement.getAttribute('data-theme')"),
                "cvd")
        finally:
            other.close()

    def test_a_new_browser_lands_in_it_and_the_settings_control_agrees(self):
        self.register("cvd_fourth")
        jar = cookie_jar(self.context)
        self.host.request("/prefs", cookies=jar, method="POST", body={"theme": "dark"})
        other = self.browser.new_context(viewport={"width": 1280, "height": 900})
        try:
            other.add_cookies([{"name": k, "value": v, "url": self.host.url("/")}
                               for k, v in jar.items()])
            page = other.new_page()
            page.goto(self.host.url("/"))
            wait_until(page, "() => !document.body.classList.contains('locked')")
            self.assertEqual(page.eval_on_selector("#themesel", "el => el.value"), "dark",
                             "a control that disagrees with the page is worse than none")
        finally:
            other.close()

    def test_the_control_sits_where_settings_belong(self):
        # Chris, 11-09: "the settings should be top right where the gravatar is".
        # It shipped top left, and a control in the wrong corner is a control
        # nobody finds twice.
        self.open_site()
        self.wait_door_rendered()
        vw = self.page.evaluate("() => innerWidth")
        door = self.page.eval_on_selector(
            "#thememountdoor .themebtn",
            "e => { const r = e.getBoundingClientRect();"
            " return {right: r.right, top: r.top}; }")
        self.assertGreater(door["right"], vw - 60, "the door gear belongs at the right edge")
        self.assertLess(door["top"], 60, "and at the top")
        # signed in it keeps the same corner, beside the avatar rather than over
        # a bar that no longer needs one on the left
        self.register("corner_reader")
        bar = self.page.eval_on_selector(
            "#thememount .themebtn",
            "e => { const r = e.getBoundingClientRect();"
            " return {right: r.right, top: r.top}; }")
        avatar = self.page.eval_on_selector(
            "#avatar", "e => { const r = e.getBoundingClientRect(); return {x: r.x}; }")
        self.assertGreater(bar["right"], vw - 200)
        self.assertLessEqual(bar["right"], avatar["x"] + 1,
                             "the gear sits to the left of the avatar, in the same corner")
        self.assertLess(bar["top"], 60)
        # and its menu opens back into the page instead of off the screen edge
        self.page.click("#thememount .themebtn")
        wait_visible(self.page, "#thememount .thememenu")
        menu = self.page.eval_on_selector(
            "#thememount .thememenu",
            "e => { const r = e.getBoundingClientRect(); return {x: r.x, right: r.right}; }")
        self.assertLessEqual(menu["right"], vw + 1)
        self.assertGreater(menu["x"], 0)
