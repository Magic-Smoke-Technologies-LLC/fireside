"""The two-factor page, driven the way a locked-out person drives it.

Four things a page can get wrong without any server test noticing, because each
one is about what a person sees after a step that otherwise succeeded:

1. The enrol screen promised "scan" and drew no QR, so the first instruction on
   the page was impossible to follow.
2. One code box feeds three buttons (change, new recovery codes, turn off) and
   only one of them said anything when the box was empty; the other posted a
   blank code and the server answered "that code did not match", which blames
   the code instead of the empty box.
3. New recovery codes were rendered and then blanked by the status refresh the
   same click had started, so they existed for one frame. That reply is the only
   time the codes exist at all.
4. Cancel on the dialog that finishes a login showed the lock screen, which
   invites a password the visitor already typed correctly.

Every test here goes through the page's own controls and asserts on text a
person reads, not on a handler's return value.
"""

import unittest

from tests_ui.support import BrowserTest, wait_visible, wait_until


class TestTwofaPage(BrowserTest):

    def open_account(self):
        self.page.click('#tabs button[data-v="set"]')
        wait_visible(self.page, "#settabs")
        self.page.click('#settabs button[data-p="account"]')
        wait_until(self.page, "() => { const e = document.querySelector('#twofastatus');"
                              " return e && e.textContent.trim().length > 0; }")

    def enrol(self):
        """Set the factor up through the account panel, as a reader would."""
        self.open_account()
        self.page.click("#twofastart")
        wait_visible(self.page, "#twofadlg")
        wait_until(self.page, "() => (document.querySelector('#twofasecret').textContent"
                              " || '').replace(/\\s/g, '').length > 8")
        secret = self.page.text_content("#twofasecret").replace(" ", "").strip()
        self.page.fill("#twofainput", self.totp_code(secret))
        self.page.click("#twofago")
        wait_visible(self.page, "#twofadlg #twofaerr button")
        self.page.click("#twofadlg #twofaerr button")     # "I saved them"
        # that button reloads the page, so the panel has to come back on its own
        wait_until(self.page, "() => { const on = document.querySelector('#twofaon');"
                              " return on && !on.hidden; }")
        return secret

    def test_the_enrol_screen_does_not_promise_a_qr_it_does_not_draw(self):
        self.register("twofa_copy")
        self.wait_in_app()
        self.open_account()
        self.page.click("#twofastart")
        wait_visible(self.page, "#twofadlg")
        sub = self.page.text_content("#twofasub")
        self.assertNotIn("Scan", sub, "there is no QR on this page, so the copy may not say scan")
        self.assertIn("key", sub)
        self.assertEqual(self.page.evaluate(
            "() => document.querySelectorAll('#twofaenrol img, #twofaenrol canvas,"
            " #twofaenrol svg').length"), 0)
        # the key itself is on screen: that is the thing a person types
        self.assertGreater(len(self.page.text_content("#twofasecret").split()), 2)

    def test_an_empty_code_box_says_so_instead_of_blaming_the_code(self):
        self.register("twofa_empty")
        self.wait_in_app()
        self.enrol()
        before = len(self.refused)
        self.page.click("#twofarecov")
        self.page.wait_for_timeout(200)
        msg = self.page.text_content("#twofamessage")
        self.assertIn("six-digit code", msg)
        self.assertIn("New recovery codes", msg)
        # nothing was sent: the server never had a chance to call it a bad code
        new_refusals = [r for r in self.refused[before:] if "/2fa/recovery" in r[1]]
        self.assertEqual(new_refusals, [])
        # a short entry gets its own sentence, not the same one
        self.page.fill("#twofacode", "123")
        self.page.click("#twofarecov")
        self.page.wait_for_timeout(200)
        self.assertIn("3 digit", self.page.text_content("#twofamessage"))

    def test_new_recovery_codes_stay_on_screen(self):
        self.register("twofa_codes")
        self.wait_in_app()
        secret = self.enrol()
        # the code that set the factor up is spent. Pressing New recovery codes
        # with it seconds later is the commonest thing a person does, and the
        # page may not call that code wrong.
        self.page.fill("#twofacode", self.totp_code(secret))
        self.page.click("#twofarecov")
        wait_until(self.page, "() => (document.querySelector('#twofamessage').textContent"
                              " || '').includes('already been used')")
        self.assertIn("30 seconds", self.page.text_content("#twofamessage"))
        # then the next window's code, which is what the phone shows a moment later
        import time
        from host import totp
        self.page.fill("#twofacode", totp.totp(secret, at=time.time() + 30))
        self.page.click("#twofarecov")
        # the status line refresh that follows must not wipe the codes
        wait_until(self.page, "() => (document.querySelector('#twofamessage').textContent"
                              " || '').includes('only time')")
        self.page.wait_for_timeout(400)
        rows = self.page.eval_on_selector_all(
            "#twofamessage pre", "els => els.map(e => e.textContent)")
        self.assertEqual(len(rows), 1, "the codes have to survive the refresh")
        codes = [c for c in rows[0].split("\n") if c.strip()]
        self.assertEqual(len(codes), 10)
        # and the status line agrees with the number just issued
        self.assertIn("10 recovery code", self.page.text_content("#twofastatus"))

    def test_a_wrong_code_names_the_clock_and_the_way_out(self):
        self.register("twofa_wrong")
        self.wait_in_app()
        secret = self.enrol()
        self.sign_out()
        self.sign_in_incomplete("twofa_wrong")
        # a code from an hour ago: definitely not the live one, and it does not
        # depend on guessing which digits are free
        import time
        from host import totp
        self.page.fill("#twofainput", totp.totp(secret, at=time.time() - 3600))
        self.page.click("#twofago")
        wait_until(self.page, "() => (document.querySelector('#twofaerr').textContent"
                              " || '').length > 0")
        err = self.page.text_content("#twofaerr")
        self.assertIn("did not match", err)
        self.assertIn("30 seconds", err, "the refresh hint has to be there")
        self.assertIn("clock", err, "a skewed clock is the common cause")
        self.assertIn("recovery code", err, "and the way back in has to be named")

    def test_cancel_is_not_a_dead_end(self):
        self.register("twofa_cancel")
        self.wait_in_app()
        self.enrol()
        self.sign_out()
        self.sign_in_incomplete("twofa_cancel")
        self.page.click("#twofacancel")
        # cancel ends the half-session and the door asks again, with its form
        wait_until(self.page, "() => document.body.classList.contains('locked')")
        wait_visible(self.page, "#lockgo")
        self.assertTrue(self.page.is_visible("#lockpass"))
        self.assertFalse(self.page.is_visible("#twofadlg"))
        # the half-session is gone: a reload does not put the dialog back
        self.page.reload()
        self.page.wait_for_timeout(600)
        self.assertFalse(self.page.is_visible("#twofadlg"),
                         "cancel left the half-session in place")

    def sign_in_incomplete(self, username, password="bench-correction-1"):
        """Sign in and stop at the factor, which is where cancel lives."""
        self.page.fill("#lockuser", username)
        self.page.fill("#lockpass", password)
        self.page.click("#lockgo")
        wait_visible(self.page, "#twofadlg")


if __name__ == "__main__":
    unittest.main()