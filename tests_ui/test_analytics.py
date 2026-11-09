"""The analytics switch, from the reader's side.

The tag itself is not something to test against Google: what matters is the
behaviour the promise rests on.

  * with no id configured, the account panel offers nothing and nothing is
    requested;
  * with an id, the tag loads for everyone (that is how the visitors who never
    sign up get counted) and the box is off, so the request that goes out is the
    cookieless one;
  * turning the box on grants consent, turning it off revokes it, and the choice
    survives a reload because it is the reader's, not the session's.

The tag request is intercepted and aborted: a test suite must not quietly post
pageviews to a live analytics property.
"""

import unittest

from tests_ui.support import BrowserTest, wait_until, wait_visible


class TestNoId(BrowserTest):

    def test_nothing_is_offered_and_nothing_is_requested(self):
        self.open_site()
        self.wait_door_rendered()
        self.assertTrue(self.page.evaluate(
            "() => document.getElementById('gaoptwrap').hidden"))
        asked = [u for (_s, u) in self.refused if "googletagmanager" in u]
        self.assertEqual(asked, [])
        self.assertFalse(self.page.evaluate("() => !!window.__fsGa"))


class TestWithId(BrowserTest):
    host_env = {"FIRESIDE_GA_ID": "G-TEST12345"}

    def _block_google(self):
        self.page.route("**://www.googletagmanager.com/**",
                        lambda route: route.abort())

    def test_the_tag_loads_for_a_visitor_who_never_signs_in(self):
        self._block_google()
        self.open_site()
        self.wait_door_rendered()
        wait_until(self.page, "() => !!window.__fsGa")
        # the consent default is on the page before the loader ran
        self.assertEqual(self.page.evaluate(
            "() => window.dataLayer.some(a => a[0] === 'consent' && a[1] === 'default')"),
            True)
        # and the grant was never sent, so it is cookieless
        self.assertEqual(self.page.evaluate(
            "() => window.dataLayer.some(a => a[0] === 'consent' && a[1] === 'update')"),
            False)

    def test_the_box_is_off_by_default_and_grants_when_ticked(self):
        self._block_google()
        self.register("ga_reader")
        # the navigation itself is covered elsewhere; this test is about the box
        self.page.evaluate("() => { show('set'); setSetTab('account', 'replace'); }")
        wait_visible(self.page, "#gaoptwrap")
        box = self.page.query_selector("#gaopt")
        self.assertFalse(box.is_checked(), "analytics starts off until the reader says so")
        self.page.check("#gaopt")
        wait_until(self.page, "() => window.dataLayer.some(a => a[0] === 'consent'"
                              " && a[1] === 'update'"
                              " && a[2].analytics_storage === 'granted')")
        self.page.uncheck("#gaopt")
        wait_until(self.page, "() => window.dataLayer.some(a => a[0] === 'consent'"
                              " && a[1] === 'update'"
                              " && a[2].analytics_storage === 'denied')")

    def test_the_choice_survives_a_reload(self):
        self._block_google()
        self.register("ga_reader2")
        self.page.evaluate("() => localStorage.setItem('fs.analytics', '1')")
        self.page.reload()
        self.wait_in_app()
        wait_until(self.page, "() => window.dataLayer.some(a => a[0] === 'consent'"
                              " && a[1] === 'update'"
                              " && a[2].analytics_storage === 'granted')")


if __name__ == "__main__":
    unittest.main()
