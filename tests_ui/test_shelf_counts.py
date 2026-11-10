"""The number that says a chapter is voiced has to follow the audio.

Chris read the shelf and got "0 of 5 voiced" over five chapters that had just
been voiced. The server was right the whole time: the page fills `books` once at
boot, and the two places that print "N of M voiced" (the shelf cell and the
Continue card) read that copy. A conversion changed the audio, the book page and
the player, and left the shelf saying what it said when the page opened.

The test drives a real conversion through the app's own dialog, with the offline
tone provider so nothing leaves the machine, then reads the rendered shelf
against the host's own answer for the same session. On a stale shelf the two
disagree, and the disagreement is exactly the bug: both numbers look plausible
and only one of them is true.
"""

import time
import unittest

from tests_ui.support import (BrowserTest, cookie_jar, wait_until,
                              wait_visible)


class TestShelfCounts(BrowserTest):

    def add_local_voice(self):
        """The offline tone provider: no key, no network, one voice."""
        status, body = self.host.request(
            "/connections", cookies=cookie_jar(self.context), method="POST",
            body={"provider": "local", "label": "Offline tone"})
        self.assertEqual(status, 200, "the host would not take a local voice: %r" % (body,))

    def wait_for_convert(self, timeout=180):
        """A conversion is done when the host's job row says so AND the page has
        finished its own loop.

        Both halves matter. The host finishes a chapter before the page polls
        for it, so reading the shelf the moment the job is done reads a page
        that is still mid-conversion, which is a test artefact rather than the
        bug under test. `RENDERJOB` is cleared in the page's own `finally`, so
        it is the page saying it is finished.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            status, jobs = self.host.request("/jobs?user=",
                                             cookies=cookie_jar(self.context))
            for j in (jobs or {}).get("jobs", []):
                if j.get("status") == "error":
                    self.fail("the conversion failed: %s" % j.get("error"))
            if any(j.get("status") == "done" for j in (jobs or {}).get("jobs", [])):
                wait_until(self.page, "() => RENDERJOB === null", timeout=30000)
                return
            time.sleep(0.5)
        self.fail("the conversion never finished\n%s" % self.host.tail())

    def test_a_voiced_chapter_moves_the_shelf_number(self):
        self.register("ui_counts")
        self.add_local_voice()

        # The book comes in first, unvoiced: the conversion is the thing under
        # test, not the import. Adding then reloading is how a person finds the
        # voice they just connected already listed in the dialog.
        status, add = self.host.request(
            "/book/example", cookies=cookie_jar(self.context), method="POST",
            body={"user": "ui_counts", "render": False})
        self.assertEqual(status, 200, "the example book did not import: %r" % (add,))
        self.page.reload()
        self.wait_library(1)
        self.open_library()
        host = self.library_via_host()[0]
        self.assertEqual(host["rendered"], 0, "the book arrived already voiced")
        self.assertEqual(self.shelf()[0]["line"], "0 of %d voiced" % host["chapters"])

        # One chapter through the real dialog: open it, tick chapter one, press
        # the button the reader presses.
        self.page.evaluate("() => openBook(%r)" % add["book_id"])
        wait_until(self.page, "() => !!book && book.id === %r" % add["book_id"])
        self.page.evaluate("() => openConvert()")
        wait_visible(self.page, "#convertdlg")
        wait_until(self.page, "() => { const v = document.querySelector('#cvvoice');"
                              " return !!(v && v.value); }")
        self.page.click("#cvlist input.chk[data-idx='0']")
        self.page.click("#cvsel")
        self.wait_for_convert()

        # The host says one chapter is voiced. So must the shelf, without a
        # reload: the audio landed while this page was open.
        books = self.library_via_host()
        self.assertEqual(books[0]["rendered"], 1,
                         "the conversion did not land, so this proves nothing")
        self.open_library()
        self.assertEqual(self.shelf()[0]["line"], "1 of %d voiced" % books[0]["chapters"],
                         "the shelf still prints the count it had at boot")

        # The Continue card prints the same number from the same copy, so it is
        # read here too rather than trusted to follow.
        card = self.page.evaluate("""() => {
            const m = document.querySelector('#resume .sub');
            return m ? m.textContent : ''; }""")
        self.assertIn("1 of %d chapters voiced" % books[0]["chapters"], card,
                      "the Continue card kept the boot count: %r" % card)
        self.assert_no_page_errors()


if __name__ == "__main__":
    unittest.main()