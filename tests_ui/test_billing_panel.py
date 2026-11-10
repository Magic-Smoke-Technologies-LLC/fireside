"""A paying reader has to be able to see the charge and stop it, from here.

Change card and Cancel renewal used to live in a settings panel that was deleted
with the plans page move, and the code behind them was deleted after it. Nobody
was exposed only because payments are off; the first live charge would have
created a subscriber with no self-serve control anywhere in the app.

What is asserted is the seam, not the form: a real host, a real account, a
subscription row written into the host's own database, and what the screen says
about it. Three states, because each one is a different lie if it is wrong:

  * a free account is told nothing is billed and gets a door to the plans,
  * a subscriber is told the date of the next charge and can cancel,
  * and a cancelled subscriber is told when it stops, not when it renews.

That last one is the reason `cancel_at` exists. Paddle keeps a cancelled
subscription `active` and schedules the change, so status alone would show a
subscriber "Renews on ..." after they had cancelled.
"""

import os
import sqlite3
import time

from tests_ui.support import BrowserTest, wait_until, wait_visible

DAY = 86400


def seed_subscription(host, user, plan="platform", status="active",
                      period_end=None, cancel_at=0):
    """Write the row the webhook would have written.

    Through the DB, not through the API: nothing in the app invents a paid
    subscription, which is the point of recording what the provider said.
    """
    path = os.path.join(host.data, "fireside.db")
    deadline = time.time() + 15
    while not os.path.exists(path) and time.time() < deadline:
        time.sleep(0.1)
    conn = sqlite3.connect(path, timeout=15)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO subscriptions(user_id, plan, status, provider,"
            " reference, current_period_end, cancel_at, customer, subscription_id,"
            " updated_at, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("local:" + user, plan, status, "paddle", "test",
             int(period_end or (time.time() + 30 * DAY)), int(cancel_at),
             "ctm_test", "sub_test", "2026-11-09", "2026-11-09"))
        conn.commit()
    finally:
        conn.close()


def open_billing(page):
    page.click("#avatar")
    page.click("#menuset")
    page.click("#settabs button[data-p='billing']")
    wait_visible(page, "#v-set .setpanel[data-p='billing']")
    wait_until(page, "() => { const n = document.getElementById('billname');"
                     " return n && !['', '\\u2026'].includes(n.textContent.trim()); }")


def buttons(page):
    return page.evaluate("""() => Array.from(
        document.querySelectorAll('#billacts button')).map(b => b.textContent.trim())""")


class TestBillingPanel(BrowserTest):

    def test_free_account_is_told_nothing_is_billed(self):
        self.register("billfree")
        open_billing(self.page)
        self.assertEqual(self.page.text_content("#billname").strip(), "Free")
        self.assertIn("No recurring charge",
                      self.page.text_content("#billrenew"))
        self.assertEqual(buttons(self.page), ["See the plans"])
        self.assert_no_page_errors()

    def test_a_subscriber_sees_the_next_charge_and_can_stop_it(self):
        user = self.register("billpaid")
        end = int(time.time() + 12 * DAY)
        seed_subscription(self.host, user, period_end=end)
        self.page.reload()
        self.wait_in_app()
        open_billing(self.page)
        self.assertEqual(self.page.text_content("#billname").strip(), "Reader")
        renew = self.page.text_content("#billrenew")
        self.assertIn("Renews on", renew)
        self.assertIn(str(time.gmtime(end).tm_mday), renew,
                      "the date on screen is not the one in the row: %r" % renew)
        self.assertEqual(buttons(self.page),
                         ["Change card or billing details", "Cancel renewal"])
        self.assert_no_page_errors()

    def test_a_cancelled_subscriber_is_told_when_it_stops(self):
        user = self.register("billstop")
        end = int(time.time() + 9 * DAY)
        seed_subscription(self.host, user, status="active",
                          period_end=end, cancel_at=end)
        self.page.reload()
        self.wait_in_app()
        open_billing(self.page)
        renew = self.page.text_content("#billrenew")
        self.assertIn("Stops on", renew)
        self.assertNotIn("Renews on", renew)
        # no second cancel on an already-cancelled row
        self.assertEqual(buttons(self.page), ["Change card or billing details"])
        self.assert_no_page_errors()