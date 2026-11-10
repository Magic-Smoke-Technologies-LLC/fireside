"""The money page has to state the deal where a buyer is deciding, and the door
has to show the providers as the brands they are.

Two complaints, both from the person who owns the product, both about a page
that was technically correct:

  * the wallet promise (never expires, charged once per chapter, our rate is
    the provider's plus a tenth) was fine print under the rate table, which is
    where a promise goes to be missed;
  * a row of identical "Continue with ..." buttons made the door read as
    paragraphs, so the only things that looked pressable were Sign in and a
    list of words.

What is asserted here is the thing a screenshot cannot: that the promise comes
before the prices in the document, that its lead line is set larger than body
copy, and that each provider is its own glyph tile with an accessible name that
says what it does.
"""

import unittest

from tests_ui.support import BrowserTest, cookie_jar, wait_until, wait_visible

GOOGLE_ID = {"FIRESIDE_OAUTH_GOOGLE_CLIENT_ID": "test-id",
             "FIRESIDE_OAUTH_GOOGLE_SECRET": "test-secret"}


class TestProviderTiles(BrowserTest):
    host_env = GOOGLE_ID

    def test_each_provider_is_its_own_glyph(self):
        self.open_site()
        self.wait_door_rendered()
        wait_visible(self.page, "#lockproviders .ssotile")
        tiles = self.page.query_selector_all("#lockproviders .ssotile")
        self.assertEqual(len(tiles), 1, "one provider is configured, so one tile")
        t = tiles[0]
        self.assertTrue(t.query_selector("svg"), "the tile needs the brand mark")
        self.assertEqual(t.get_attribute("aria-label"), "Continue with Google")
        self.assertIn("Google", t.text_content())
        self.assert_no_page_errors()

    def test_a_provider_button_actually_starts_the_flow(self):
        # A pretty tile that does nothing is worse than the word button it
        # replaced: clicking it must hit the provider's start route.
        self.open_site()
        self.wait_door_rendered()
        wait_visible(self.page, "#lockproviders .ssotile")
        with self.page.expect_navigation(wait_until="commit", timeout=15000):
            self.page.click("#lockproviders .ssotile")
        self.assertIn("accounts.google.com", self.page.url)


class TestWalletPromiseIsVisible(BrowserTest):

    def test_the_promise_comes_before_the_prices(self):
        self.page.goto(self.host.url("/pricing"))
        wait_visible(self.page, "#walletpromise")
        order = self.page.evaluate("""() => {
            const w = document.querySelector('#walletpromise');
            const p = document.querySelector('#pageplans');
            return !!(w.compareDocumentPosition(p) & Node.DOCUMENT_POSITION_FOLLOWING);
        }""")
        self.assertTrue(order, "the promise has to be read before the price cards")

    def test_its_lead_line_is_not_fine_print(self):
        self.page.goto(self.host.url("/pricing"))
        wait_visible(self.page, "#walletpromise")
        lead = self.page.evaluate(
            "() => parseFloat(getComputedStyle(document.querySelector('#walletpromise .wplead')).fontSize)")
        body = self.page.evaluate(
            "() => parseFloat(getComputedStyle(document.body).fontSize)")
        self.assertGreaterEqual(lead, 18, "the promise is set in body copy size")
        self.assertGreater(lead, body)

    def test_the_words_are_the_ones_the_terms_keep(self):
        self.page.goto(self.host.url("/pricing"))
        wait_visible(self.page, "#walletpromise")
        text = self.page.text_content("#walletpromise")
        self.assertIn("never expire", text)
        self.assertIn("pay for services on the platform", text)
        self.assertIn("provider", text)


class TestRoomIsWhatAPlanBuys(BrowserTest):
    """Chris, 11-09: an incentive to buy the platform, and the wallet is the one
    door that stays open on free. So the free reader may put dollars in the
    wallet, and the room on the shelf is what a plan sells: no block button on
    free, and the server refuses the same order even if the button came back.

    The paid case is the positive control for the same screen: the block button
    has to still be there for a plan, or the test would pass by hiding it from
    everyone."""

    def _buys(self):
        self.page.goto(self.host.url("/pricing"))
        wait_visible(self.page, "#pagebuys")
        wait_until(self.page, "() => document.querySelectorAll('#pagebuys h3').length >= 2")
        return (self.page.text_content("#pagebuys"),
                [b.inner_text().strip() for b in
                 self.page.query_selector_all("#pagebuys section button")])

    def test_free_funds_the_wallet_and_is_sent_to_the_plans(self):
        self.register("freebuyer")
        text, labels = self._buys()
        self.assertIn("Add funds", text, "the wallet is the door that stays open")
        self.assertIn("Storage comes with a plan", text)
        self.assertIn("See the plans", labels)
        self.assertNotIn("Buy blocks", labels, "room is not for sale on free")
        self.assert_no_page_errors()

    def test_a_plan_still_sells_blocks(self):
        self.register("paidbuyer")   # the first account on this host is the admin
        _status, me = self.host.request("/me", cookies=cookie_jar(self.context))
        uid = (me or {}).get("user")
        self.assertTrue(uid, "the browser has no session, so /me answered nothing")
        status, _ = self.host.request(
            "/admin/plan", cookies=cookie_jar(self.context), method="POST",
            body={"user": uid, "plan": "platform"})
        self.assertEqual(status, 200, "the comp has to land before the page is read")
        text, labels = self._buys()
        self.assertIn("More storage", text)
        self.assertIn("Buy blocks", labels, "a paid plan still buys a mid-cycle block")


class TestSignedInPlansPage(BrowserTest):
    """Chris, 11-10: signed in on /pricing he did not see all the plans.

    The page builds one card per tier from the catalog. The current tier took
    the branch that disables its cycle buttons, and the reference it disabled
    with was a `const` scoped to an if-block above it in the same function. That
    threw on the holder's own card, and because the cards are appended inside a
    forEach, the throw also dropped that card and every card after it. Signed
    out nothing is current, so the bug only appeared for a paying reader: the
    one person who might buy again.

    Two controls: the page a comped paid plan sees, and planCard itself against
    a provider config with prices. The host runs with a fake Paddle key so the
    page takes the same branch a real deployment does (a registered provider
    with prices is what puts the cycle bar on the card).
    """

    host_env = {"FIRESIDE_PAYMENT_PROVIDER": "host.payments_paddle",
                "FIRESIDE_PADDLE_API_KEY": "test_api_key",
                "FIRESIDE_PADDLE_WEBHOOK_SECRET": "whsec_test",
                "FIRESIDE_PADDLE_ENV": "sandbox"}

    def _paid(self, user):
        self.register(user)
        _status, me = self.host.request("/me", cookies=cookie_jar(self.context))
        uid = (me or {}).get("user")
        self.assertTrue(uid, "the browser has no session")
        status, _ = self.host.request(
            "/admin/plan", cookies=cookie_jar(self.context), method="POST",
            body={"user": uid, "plan": "platform"})
        self.assertEqual(status, 200, "the comp has to land before the page is read")

    def test_a_paid_reader_sees_every_plan_including_their_own(self):
        self._paid("planbuyer")
        self.page.goto(self.host.url("/pricing"))
        wait_until(self.page, "() => !!document.querySelector('#v-plans.on')")
        # The static markup holds the same four cards as a no-JS fallback, so
        # the count alone is green before the host has answered. The buy row is
        # built only by planCard, so it is the receipt that the dynamic render
        # ran, which is the render this test is about.
        wait_until(self.page, "() => document.querySelector('#pageplans .prcard .buy')"
                              " !== null")
        labels = [c.inner_text().split("\n")[0].strip() for c in
                  self.page.query_selector_all("#pageplans .prcard .rung")]
        self.assertEqual(labels, ["Free", "Reader", "Listener", "Studio"],
                         "a signed-in plan dropped its own card and the ones after it")
        current = self.page.query_selector_all("#pageplans .prcard.current")
        self.assertEqual(len(current), 1, "exactly the plan they hold is current")
        self.assertIn("Reader", current[0].inner_text())
        self.assertEqual(self.page.evaluate(
            "() => Array.from(document.querySelectorAll('#pageplans .prcard'))"
            ".filter(c => c.getBoundingClientRect().width < 50).length"), 0,
            "every card has to have width, not just exist in the DOM")
        self.assert_no_page_errors()

    def test_the_current_card_disables_its_cycle_without_throwing(self):
        # Positive control for the crash the page above can only reach with a
        # provider configured: give planCard prices for both cycles and ask it
        # for the plan the reader already holds.
        self.page.goto(self.host.url("/pricing"))
        wait_until(self.page, "() => !!document.querySelector('#v-plans.on')")
        out = self.page.evaluate("""() => {
            const t = {id: 'platform', label: 'Reader', amount_cents: null,
                       list_amount_cents: 500, list_annual_cents: 3600,
                       included_usd: 1.0, included_bytes: 2000000000};
            const cat = {payments_configured: true,
                         tiers: [{id: 'free', label: 'Free'}, t]};
            const cfg = {prices: {platform: {month: 'pri_m', year: 'pri_y'}}};
            const card = planCard(t, 'platform', cat, cfg);
            const cyc = Array.from(card.querySelectorAll('.cycle button'));
            return {cycle: cyc.length,
                    cycleDisabled: cyc.every(b => b.disabled),
                    goDisabled: Array.from(card.querySelectorAll('.btn'))
                                  .every(b => b.disabled)};
        }""")
        self.assertEqual(out["cycle"], 2, "both cycles are offered")
        self.assertTrue(out["cycleDisabled"], "a held plan cannot be bought again")
        self.assertTrue(out["goDisabled"])
        self.assert_no_page_errors()
