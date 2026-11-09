"""The first screen has to say what the product is before it asks for a password.

The complaint this answers is a real one, in a user's words: the landing screen
was a form, the price list was a hidden tab, and nobody could tell what they were
signing up for. So the door is two panes now: what fireside does and what it
costs on one side, the way in on the other.

Three things are checked, and each of them is invisible from a passing markup
test:

  * a stranger landing on / sees the welcome copy and a working link to /pricing;
  * on a wide window the form sits beside the copy, not on top of it or under it;
  * on a phone the copy does not push the form off the screen, and the button in
    the copy still lands the reader in the form.
"""

import unittest

from tests_ui.support import BrowserTest, wait_until, wait_visible


class TestWelcomeDoor(BrowserTest):

    def test_a_stranger_is_told_what_this_is(self):
        self.open_site()
        self.wait_door_rendered()
        self.assertTrue(self.page.is_visible("#lockwel"))
        headline = self.page.text_content("#lockwel .welh").strip()
        self.assertTrue(headline, "the welcome pane needs a headline, not just a form")
        # the price list was reachable only from a tab a stranger never saw
        link = self.page.query_selector("#lockwel .welactions a[href='/pricing']")
        self.assertIsNotNone(link, "the welcome copy has to point at the prices")
        self.assertTrue(link.is_visible())
        self.assertIn("pricing", link.text_content().lower())
        # and the promise on the page is the promise the webhook keeps
        self.assertIn("$3", self.page.text_content("#lockwel .welcred"))
        self.assert_no_page_errors()

    def test_the_form_sits_beside_the_copy_not_on_top_of_it(self):
        self.open_site()
        self.wait_door_rendered()
        wel = self.page.query_selector("#lockwel").bounding_box()
        card = self.page.query_selector("#lockform").bounding_box()
        self.assertGreater(card["x"], wel["x"] + wel["width"] - 2,
                           "on a wide window the login belongs to the side")
        # the two panes are on one line: the form is not below the copy
        self.assertLess(abs(card["y"] - wel["y"]), 220)

    def test_a_phone_gets_the_form_without_scrolling_past_an_essay(self):
        self.page.set_viewport_size({"width": 360, "height": 640})
        self.open_site()
        self.wait_door_rendered()
        card = self.page.query_selector("#lockform").bounding_box()
        # the form starts inside the first screenful, not three paragraphs down
        self.assertLess(card["y"], 640,
                        "a phone visitor must reach the form without hunting")
        self.assertTrue(self.page.is_visible("#lockuser"))

    def test_the_call_to_action_lands_in_the_form(self):
        self.open_site()
        self.wait_door_rendered()
        self.page.set_viewport_size({"width": 360, "height": 640})
        self.page.click("#welstart")
        wait_until(self.page, "() => document.activeElement && "
                              "document.activeElement.id === 'lockuser'")
        self.assertTrue(self.page.is_visible("#lockemailwrap"),
                        "the call to action creates an account, so it asks for an email")


if __name__ == "__main__":
    unittest.main()

    def test_the_rates_are_a_table_and_the_prose_names_no_amount(self):
        """A price inside a sentence goes stale silently.

        The door used to say "$19.63 per million characters" in the middle of a
        step, so the day the provider repriced, the front page became a lie in
        the one place nobody re-reads. The number belongs in a table with a
        label, and the sentence points at it. Both halves are checked: the
        table has the rate, and the prose does not.
        """
        self.open_site()
        self.wait_door_rendered()
        table = self.page.query_selector("#lockwel table.welrates")
        self.assertIsNotNone(table, "the door needs a rate table, not a sentence")
        text = table.text_content()
        self.assertIn("19.63", text)
        self.assertIn("million characters", text)
        rows = self.page.query_selector_all("#lockwel .welrates tr")
        self.assertGreaterEqual(len(rows), 3, "a table of one row is a sentence")
        prose = " ".join(el.text_content() for el in
                         self.page.query_selector_all("#lockwel .welsteps li span"))
        self.assertNotIn("$19.63", prose,
                         "the prose carries no amount; the table does")
        self.assert_no_page_errors()

