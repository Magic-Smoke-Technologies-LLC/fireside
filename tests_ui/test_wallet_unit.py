"""The wallet is a money screen, and it has to speak money.

Chris, 2026-11-09, looking at his own wallet: "Looks like copy is still there
about character count?" Two things were behind that. The balance row still
holds characters from before the switch, and the header chip hardcoded "chars"
no matter what unit the server sent, so even a dollar account read "1 chars".

These prove the seam rather than the formatter: a real host, a real account, a
real balance row written into the host's own database, and the number the page
paints. Three states, because each one is a different lie if it is wrong:

  * a dollar balance reads in dollars,
  * a legacy character balance reads in dollars at the sale rate,
  * and a balance with no rate behind it still names the unit it is in,
    because inventing a dollar figure is worse than saying characters.
"""

import os
import sqlite3
import time
import unittest

from tests_ui.support import BrowserTest, wait_until, wait_visible

SALE = '{"connection_id":"sale1","provider":"azure"}'


def seed_balance(host, user, currency, amount, with_sale=True):
    """Put a balance (and a sale connection) into the host's own database.

    Through the DB and not through the API: no endpoint invents a balance,
    which is the point of a ledger. This is the one place a test is allowed to
    write like the accountant would.
    """
    path = os.path.join(host.data, "fireside.db")
    deadline = time.time() + 15
    while not os.path.exists(path) and time.time() < deadline:
        time.sleep(0.1)
    conn = sqlite3.connect(path, timeout=15)
    try:
        if with_sale:
            conn.execute(
                "INSERT OR REPLACE INTO connections(id,owner_id,scope,provider,label,data,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                ("sale1", "local:" + user, "user", "azure", "test voice",
                 '{"voice":"en-US-JennyNeural"}', "2026-11-09", "2026-11-09"))
        conn.execute(
            "INSERT OR REPLACE INTO balances(user_id,currency,amount) VALUES(?,?,?)",
            ("local:" + user, currency, amount))
        conn.commit()
    finally:
        conn.close()


def wait_chip(page):
    wait_until(page, "() => { const c = document.getElementById('balchip');"
                     " return c && !c.hidden && c.textContent.trim()"
                     " && c.textContent.trim() !== '\\u2014'; }")
    return page.text_content("#balchip").strip()


def open_wallet(page):
    page.click("#avatar")
    page.click("#menuset")
    page.click("#settabs button[data-p='wallet']")
    wait_visible(page, "#v-set .setpanel[data-p='wallet']")
    wait_until(page, "() => document.getElementById('wbal')"
                     ".textContent.trim() !== '\\u2026'")


class TestWalletSpeaksMoney(BrowserTest):
    host_env = {"FIRESIDE_SALE_CONNECTION": SALE}

    def test_a_dollar_balance_reads_in_dollars(self):
        user = self.register("walletusd")
        seed_balance(self.host, user, "USD", 1.0)
        self.page.reload()
        self.wait_in_app()
        self.assertEqual(wait_chip(self.page), "$1.00")
        open_wallet(self.page)
        self.assertEqual(self.page.text_content("#wbal").strip(), "$1.00")

    def test_a_legacy_character_balance_reads_in_dollars(self):
        user = self.register("walletoid")
        seed_balance(self.host, user, "chars", 50000)
        self.page.reload()
        self.wait_in_app()
        # 50,000 characters at 19.63 per million is 98 cents, and the chip is
        # the number a reader looks at all day
        self.assertEqual(wait_chip(self.page), "$0.98")
        open_wallet(self.page)
        self.assertEqual(self.page.text_content("#wbal").strip(), "$0.98")
        note = self.page.text_content("#wnote").lower()
        self.assertNotIn("still holds characters", note)
        self.assertIn("dollars", note)

    def test_the_top_up_table_has_no_character_column(self):
        user = self.register("walletrate")
        seed_balance(self.host, user, "USD", 5.0)
        self.page.reload()
        self.wait_in_app()
        open_wallet(self.page)
        wait_visible(self.page, "#wrate:not([hidden]) #wratetbl")
        heads = self.page.evaluate(
            "() => Array.from(document.querySelectorAll('#wratetbl thead th'))"
            ".map(e => e.textContent.trim())")
        self.assertEqual(heads, ["Add", "Listening at normal speed"])
        first = self.page.evaluate(
            "() => Array.from(document.querySelectorAll('#wratetbl tbody tr'))[0]"
            ".children[0].textContent.trim()")
        self.assertEqual(first, "$5.00")


class TestNoRate(BrowserTest):
    """A host selling nothing: the same balance row, no rate to convert with."""

    def test_the_unit_is_named_not_invented(self):
        user = self.register("walletnorate")
        seed_balance(self.host, user, "chars", 50000, with_sale=False)
        self.page.reload()
        self.wait_in_app()
        self.assertEqual(wait_chip(self.page), "50,000 chars")


if __name__ == "__main__":
    unittest.main()