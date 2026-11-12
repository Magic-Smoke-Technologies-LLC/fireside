"""The checkout page in a real browser, in both of its states.

The served bytes cannot tell you which state a page ends in: both sentences are
in the script either way. So this reads the page after its script ran, and
clicks the button the way a stranger would.

  * with no provider, the page says payment is not open and the button does
    nothing;
  * with a provider, the button carries the price the catalog says, and a
    visitor with no account is sent to the pricing page with the plan they
    chose instead of an overlay that cannot attach to an account.
"""

import unittest

from tests_ui.support import BrowserTest, wait_until

PADDLE_ON = {"FIRESIDE_PAYMENT_PROVIDER": "host.payments_paddle",
             "FIRESIDE_PADDLE_API_KEY": "pdl_sdbx_apikey_test",
             "FIRESIDE_PADDLE_WEBHOOK_SECRET": "pdl_ntfset_test",
             "FIRESIDE_PADDLE_CLIENT_TOKEN": "test_client_token",
             "FIRESIDE_PADDLE_ENV": "sandbox"}


class CheckoutCase(BrowserTest):

    def open_checkout(self, query=""):
        self.page.goto(self.host.url("/checkout" + query))
        wait_until(self.page, "() => document.getElementById('status')"
                              ".textContent.indexOf('Loading') < 0")
        return self.page


class TestNoProvider(CheckoutCase):

    def test_the_prices_are_on_the_page_and_the_button_says_it_is_closed(self):
        self.open_checkout("?plan=listener&interval=year")
        self.assertEqual(self.page.text_content("#status").strip(),
                         "Payment is not open yet")
        self.assertIn("$96.00 a year", self.page.text_content("#orderLine"))
        self.assertIn("Pay (disabled)", self.page.text_content("#pay"))
        self.assertEqual(self.page.get_attribute("#pay", "aria-disabled"), "true")
        self.assertEqual(self.page.text_content("#orderTitle").strip(), "Listener plan")

    def test_pressing_it_charges_nothing_and_moves_nowhere(self):
        self.open_checkout()
        before = self.page.url
        # .click() would refuse to press a button the page marks disabled, which
        # is the browser enforcing the same promise. Press it anyway.
        self.page.evaluate("() => document.getElementById('pay').click()")
        self.page.wait_for_timeout(300)
        self.assertEqual(self.page.url, before)


class TestProviderOn(CheckoutCase):
    host_env = PADDLE_ON

    def test_the_button_carries_the_price_the_catalog_says(self):
        self.open_checkout("?plan=studio&interval=month")
        self.assertEqual(self.page.text_content("#status").strip(), "Payment is open")
        self.assertEqual(self.page.text_content("#pay").strip(), "Pay $22.00 a month")
        self.assertEqual(self.page.get_attribute("#pay", "aria-disabled"), "false")

    def test_a_stranger_is_sent_to_the_pricing_page_with_their_plan(self):
        # Paddle's card form needs an account to attach the subscription to.
        # Without one the choice goes to the page that can make the account,
        # and it survives the trip in the URL.
        self.page.route("**://cdn.paddle.com/**", lambda route: route.abort())
        self.open_checkout("?plan=listener&interval=year")
        self.page.click("#pay")
        wait_until(self.page, "() => location.pathname === '/pricing'")
        wait_until(self.page, "() => !!localStorage.getItem('fireside.cart')")
        self.assertIn("plan=listener", self.page.url)
        self.assertIn("cycle=year", self.page.url)
        cart = self.page.evaluate("() => JSON.parse(localStorage.getItem('fireside.cart') || '{}')")
        self.assertEqual(cart.get("tier"), "listener")
        self.assertEqual(cart.get("cycle"), "year")

    def test_an_unpriced_plan_says_so_instead_of_opening_a_form(self):
        # Production ids that were never minted: the page must not open an
        # overlay that Paddle will refuse, and it must not pretend it can pay.
        self.host.stop()
        env = dict(PADDLE_ON)
        # A yearly id with no monthly one: the page is opened on the monthly
        # cycle, so the button has no price to hand Paddle.
        env["FIRESIDE_PADDLE_PLAN_PRICES"] = '{"platform": {"year": "pri_live_y"}}'
        env["FIRESIDE_PADDLE_PRICES"] = '{"pri_live_y": "platform"}'
        from tests_ui.support import LocalHost
        self.host = LocalHost(env_extra=env)
        self.open_checkout("?plan=platform")
        self.assertIn("not priced yet", self.page.text_content("#pay"))


if __name__ == "__main__":
    unittest.main()