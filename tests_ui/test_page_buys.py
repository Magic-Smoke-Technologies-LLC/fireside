"""The two one-time things on the pricing page, pressed in a real browser.

Found 2026-11-13 by reading the served page's handlers against the provider,
not by watching a click: the storage button asked the server for a checkout
that Paddle answers with "go to /pricing", which is the page it was pressed on,
and the wallet button posted to /credits/purchase, which asks the provider for a
hosted one-time checkout Paddle has never had. One button looped, the other
opened nothing, and the page had printed a price for both.

So this presses them with Paddle's overlay replaced by a recorder: what is
being tested is the handoff (which price id, how many blocks), not the CDN.
"""

import unittest

from tests_ui.support import BrowserTest, cookie_jar, wait_until

PADDLE_ON = {"FIRESIDE_PAYMENT_PROVIDER": "host.payments_paddle",
             "FIRESIDE_PADDLE_API_KEY": "pdl_sdbx_apikey_test",
             "FIRESIDE_PADDLE_WEBHOOK_SECRET": "pdl_ntfset_test",
             "FIRESIDE_PADDLE_CLIENT_TOKEN": "test_client_token",
             "FIRESIDE_PADDLE_ENV": "sandbox",
             # the page must not wait on Paddle for a price: this host has no
             # route to the API, so the catalog read fails and the page falls
             # back to its list price. The ids (what charges the card) are ours.
             "FIRESIDE_PADDLE_PLAN_PRICES": "{}"}

RECORD = """
window.__opened = [];
window.Paddle = {
  Environment: { set: function () {} },
  Initialize: function () {},
  Checkout: { open: function (o) { window.__opened.push(o); } }
};
"""


class PageBuys(BrowserTest):
    host_env = PADDLE_ON

    def setUp(self):
        super().setUp()
        self.page.add_init_script(RECORD)

    def open_pricing_signed_in(self, plan="free"):
        self.register("buys1")
        jar = cookie_jar(self.context)
        _s, me = self.host.request("/me", cookies=jar)
        if plan != "free":
            self.host.request("/admin/plan", cookies=jar, method="POST",
                              body={"user": me["user"], "plan": plan})
        self.page.goto(self.host.url("/pricing"))
        wait_until(self.page, "() => document.querySelector('#wpact button') !== null")
        return self.page

    def opened(self):
        return self.page.evaluate("() => window.__opened || []")

    def test_a_wallet_load_opens_the_overlay_at_the_five_dollar_price(self):
        self.open_pricing_signed_in()
        cfg = self.page.evaluate("() => (BILL_CFG || {})")
        want = (cfg.get("wallet") or {}).get("5")
        self.assertTrue(want, "the page was handed no $5 wallet price id")
        self.page.click("#wpact button")
        wait_until(self.page, "() => (window.__opened || []).length > 0")
        got = self.opened()[0]
        self.assertEqual(got["items"][0]["priceId"], want)
        self.assertEqual(got["items"][0]["quantity"], 1)
        # no server round trip to a method the provider does not have
        self.assertFalse([u for (_s, u) in self.refused if "credits/purchase" in u])

    def test_buying_two_storage_blocks_opens_the_overlay_with_a_quantity(self):
        self.open_pricing_signed_in(plan="platform")
        cfg = self.page.evaluate("() => (BILL_CFG || {})")
        want = (cfg.get("storage_price") or {}).get("price_id")
        self.assertTrue(want, "the page was handed no storage price id")
        self.page.fill("#pagebuys input[type=number]", "2")
        self.page.click("text=Buy blocks")
        wait_until(self.page, "() => (window.__opened || []).length > 0")
        got = self.opened()[0]
        self.assertEqual(got["items"][0]["priceId"], want)
        self.assertEqual(got["items"][0]["quantity"], 2)

    def test_the_storage_button_is_not_dead_when_the_price_read_failed(self):
        # The id is what charges the card; a failed catalog read must not make
        # the offer unsellable while it is still on the page.
        self.open_pricing_signed_in(plan="platform")
        btn = self.page.query_selector("text=Buy blocks")
        self.assertIsNotNone(btn)
        self.assertFalse(btn.is_disabled())


if __name__ == "__main__":
    unittest.main()


class SecondBlock(BrowserTest):
    """A block already held: the button must raise the subscription, not open one.

    Buying the same block twice in sandbox on 2026-11-13 opened two $2/mo
    subscriptions and left the account with one block. The card form is the
    first block's door; after that the quantity moves on the server.
    """

    host_env = PADDLE_ON

    def setUp(self):
        super().setUp()
        self.page.add_init_script(RECORD)

    def opened(self):
        return self.page.evaluate("() => window.__opened || []")

    def _seed_block(self, uid):
        import sqlite3
        conn = sqlite3.connect(self.host.data + "/fireside.db", timeout=10)
        conn.execute(
            "INSERT INTO addons(user_id, addon, quantity, status, provider, "
            "reference, current_period_end, cancel_at, customer, subscription_id, "
            "updated_at, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (uid, "storage", 1, "active", "paddle", "sub_s1", 2000000000, 0,
             "ctm_x", "sub_s1", "2026-11-13", "2026-11-13"))
        conn.commit()
        conn.close()

    def test_the_button_raises_one_subscription_instead_of_opening_another(self):
        self.register("block2")
        jar = cookie_jar(self.context)
        _s, me = self.host.request("/me", cookies=jar)
        self.host.request("/admin/plan", cookies=jar, method="POST",
                          body={"user": me["user"], "plan": "platform"})
        self._seed_block(me["user"])
        seen = []
        self.page.on("request", lambda r: seen.append(r.url))
        self.page.goto(self.host.url("/pricing"))
        wait_until(self.page, "() => document.querySelector('#pagebuys input[type=number]') !== null")
        self.page.click("text=Buy blocks")
        wait_until(self.page, "() => (window.__opened || []).length > 0"
                              " || document.querySelector('#pagebuys') !== null")
        # the overlay is the wrong door once a block is held: no checkout opens
        self.assertEqual(self.opened(), [])
        # the server route was the one taken (it refuses here only because this
        # host has no route to Paddle; the row still holds one block)
        self.assertTrue([u for u in seen if u.endswith("/billing/storage")], seen)
        _s, blocks = self.host.request("/storage", cookies=jar)
        self.assertEqual(blocks["storage_blocks"], 1)
