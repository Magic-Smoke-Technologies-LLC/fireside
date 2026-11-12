"""The admin payments screen has to turn a saved key into a working provider.

The failure this exists for is the one a paste-into-Azure checklist could not
catch: a value that looks saved, is stored, is echoed back as "set", and never
reaches the code that charges a card. So the test does not stop at the DOM. It
presses Save in the browser, then asks the host's own /billing/config whether the
page could now open a checkout.

The second half is the security shape: a secret must never come back to the page.
A screen that prints the API key back is a key in every screenshot and a key in
every browser history.
"""

from tests_ui.support import BrowserTest, wait_until, wait_visible

SANDBOX_KEY = "pdl_sdbx_apikey_01abcdefghijklmnop"


def open_admin(page):
    page.click("#navadmin")
    wait_visible(page, "#v-adm")
    wait_visible(page, "#billfields .billfield")


def billing_config(page):
    """What the page's own fetch of /billing/config says, not a copy of it."""
    return page.evaluate("""async () => {
        const r = await fetch('/billing/config');
        return await r.json();
    }""")


class TestAdminPayments(BrowserTest):
    # The deployment names the provider module; the keys come from the screen.
    host_env = {"FIRESIDE_PAYMENT_PROVIDER": "host.payments_paddle"}

    def test_saving_a_key_makes_the_pricing_page_able_to_charge(self):
        self.register("boss")          # the first account is the admin
        open_admin(self.page)

        # nothing is configured on a fresh box, and the screen says so
        self.assertIn("No payment provider is registered",
                      self.page.inner_text("#billstatus"))
        self.assertFalse(billing_config(self.page)["ok"])

        self.page.fill("#bill_paddle_api_key", SANDBOX_KEY)
        self.page.fill("#bill_paddle_webhook_secret", "pdl_ntfset_secret")
        self.page.fill("#bill_paddle_client_token", "test_abcd")
        self.page.query_selector(
            "input[data-tier='platform'][data-cycle='month']").fill("pri_aaa")
        self.page.query_selector(
            "input[data-tier='platform'][data-cycle='year']").fill("pri_bbb")
        self.page.click("#billsave")
        wait_until(self.page,
                   "() => /Saved/.test(document.querySelector('#billmsg').textContent)")

        # the receipt is the host's answer, not the screen's own copy
        co = billing_config(self.page)
        self.assertTrue(co["ok"], co)
        self.assertEqual(co["env"], "sandbox")
        self.assertEqual(co["prices"]["platform"]["year"], "pri_bbb")
        self.assertTrue(co["payments_configured"])

        # the secret came back as a hint, never as itself
        self.assertEqual(self.page.input_value("#bill_paddle_api_key"), "")
        body = self.page.content()
        self.assertNotIn(SANDBOX_KEY, body)
        self.assertNotIn("pdl_ntfset_secret", body)
        self.assertIn("saved:", self.page.evaluate(
            "() => document.querySelector('#bill_paddle_api_key').placeholder"))

        # reload: the setting is applied at boot, not just in this session
        self.page.reload()
        wait_until(self.page, "() => document.body.classList.contains('locked') === false")
        co2 = billing_config(self.page)
        self.assertTrue(co2["ok"])
        self.assertEqual(co2["client_token"], "test_abcd")

    def test_a_sandbox_key_against_production_is_refused_and_says_why(self):
        self.register("boss")
        open_admin(self.page)
        self.page.select_option("#bill_paddle_env", "production")
        self.page.fill("#bill_paddle_api_key", SANDBOX_KEY)
        self.page.click("#billsave")
        wait_until(self.page,
                   "() => document.querySelector('#billmsg').textContent"
                   ".length > 0 && !/Saving/.test(document.querySelector('#billmsg')"
                   ".textContent)")
        msg = self.page.inner_text("#billmsg")
        self.assertIn("paddle_env", msg)
        self.assertFalse(billing_config(self.page)["ok"])