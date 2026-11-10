"""No network is not a fresh server.

Chris opened readfireside.com with no internet and the door told him to create
the first account, which on this app means "you will be the admin". Nothing on
the server had changed: the service worker served the cached shell, the request
for /auth/policy never arrived, and the page fell back to its own defaults,
where hasAccounts starts false. "I don't know" was being printed as "nobody has
ever signed up here".

The fix is to treat an unanswered host as unanswered. This test takes a real
host with a real account on it, drops the browser's connection, reloads, and
reads the door. On the old code the title is "set up fireside" and the sub line
offers the admin account. With the fix it says it can't reach fireside and the
only button asks to try again, which is also checked: reconnect, press it, and
the sign-in door comes back.
"""

import unittest

from tests_ui.support import BrowserTest, wait_until, wait_visible


class TestOfflineDoor(BrowserTest):

    def test_reload_with_no_network_never_offers_the_admin_account(self):
        # One account exists, so the host's own answer is has_accounts: true.
        # A door that says otherwise is the client inventing it.
        self.register("ui_offline")
        self.open_site()
        self.wait_in_app()

        self.context.set_offline(True)
        try:
            self.page.reload()
        except Exception:
            # a reload can legitimately raise once the connection is gone; the
            # page it is showing is what matters, so ask for it again
            try:
                self.page.goto(self.host.url("/"))
            except Exception:
                pass
        wait_visible(self.page, "#lock")
        wait_until(self.page, "() => document.querySelector('#locksub')"
                              ".textContent.trim().length > 0")

        title = self.page.text_content("#locktitle").strip()
        sub = self.page.text_content("#locksub").strip()
        self.assertEqual(title, "can't reach fireside",
                         "an offline app drew some other door: %r / %r" % (title, sub))
        self.assertNotIn("first account", sub,
                         "the door offered the admin account from a guessed state")
        self.assertEqual(self.page.text_content("#lockgo").strip(), "Try again")

        # The retry is the way back, and it has to work on its own: the button
        # reloads, the connection is up again, the host answers, and the door
        # that comes back is a sign-in (nothing logs itself back in by magic).
        self.context.set_offline(False)
        self.page.click("#lockgo")
        wait_until(self.page, "() => document.querySelector('#locktitle')"
                              ".textContent.trim() === 'sign in'")
        self.assertEqual(self.page.text_content("#locktitle").strip(), "sign in")

    def test_a_fresh_host_still_offers_to_make_the_first_admin(self):
        """The positive control. A rule that only stops something is not a rule:
        the same door must still say "create the first account" when the host
        genuinely has none, which is why the offline case cannot simply refuse
        to ever draw that sentence."""
        self.open_site()
        self.wait_door_rendered()
        self.assertEqual(self.page.text_content("#locktitle").strip(), "set up fireside")
        self.assertIn("create the first account", self.page.text_content("#locksub"))


if __name__ == "__main__":
    unittest.main()