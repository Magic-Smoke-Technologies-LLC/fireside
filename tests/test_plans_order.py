"""What the price list says first, and in what order.

Chris, 11-09 promoted "On every plan" and "Run it yourself" above the price
cards. On 11-10 he read the rendered page again and re-cut the order, to:

    intro (two lines), billing control, plans, then "The voice is metered
    separately" with the wallet callout and rate table, then "On every plan",
    "Run it yourself," support.

The 11-09 promotion is superseded, not violated: what every plan includes is
still a full section with its own heading, it just reads after the thing it is
included in. This test is source order, not a screenshot: the cards are built
from the live catalog at load, but the sections around them are static.
"""

import io
import os
import re
import unittest

PAGE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "ui", "index.html")


def plans_section():
    src = io.open(PAGE, encoding="utf-8").read()
    m = re.search(r'<section class="view" id="v-plans">(.*?)</section>', src, re.S)
    assert m, "the price list lost its section"
    return m.group(1)


def at(body, needle):
    i = body.find(needle)
    assert i >= 0, "the plans page no longer has %r" % needle
    return i


class TestPlansOrder(unittest.TestCase):

    def test_the_intro_and_the_control_come_before_the_cards(self):
        body = plans_section()
        self.assertLess(at(body, 'id="plansays"'), at(body, 'id="plancycle"'))
        self.assertLess(at(body, 'id="plancycle"'), at(body, 'id="pageplans"'))

    def test_the_voice_sits_after_the_cards(self):
        body = plans_section()
        self.assertLess(at(body, 'id="pageplans"'),
                        at(body, 'data-t="The voice is metered separately"'))

    def test_the_wallet_promise_is_read_before_the_rate_it_governs(self):
        body = plans_section()
        self.assertLess(at(body, 'id="walletpromise"'), at(body, 'id="pagerates"'))

    def test_on_every_plan_reads_after_the_prices(self):
        body = plans_section()
        self.assertLess(at(body, 'id="pageplans"'), at(body, 'data-t="On every plan"'))

    def test_run_it_yourself_is_last_of_the_selling_sections(self):
        body = plans_section()
        self.assertLess(at(body, 'data-t="On every plan"'),
                        at(body, 'data-t="Run it yourself, free"'))
        self.assertLess(at(body, 'data-t="Run it yourself, free"'),
                        at(body, 'data-t="Questions, refunds, cancellations"'))

    def test_the_rate_table_is_the_only_one_on_the_page(self):
        # Two tables held the same $19.63 and one of them was going to drift.
        body = plans_section()
        self.assertEqual(body.count('class="ratetable"'), 1)


if __name__ == "__main__":
    unittest.main()


class TestTheKokoroPath(unittest.TestCase):
    """"Run it yourself, free" has to lead somewhere. An invitation to
    self-host with no instructions is a dead end for the reader most likely to
    take it."""

    def test_the_plans_page_links_the_guide(self):
        body = plans_section()
        self.assertIn('href="/kokoro"', body)

    def test_the_guide_is_public(self):
        from host.app import GET_ROUTES, PUBLIC_ROUTES
        self.assertEqual(GET_ROUTES.get("/kokoro"), "kokoro")
        self.assertIn("/kokoro", PUBLIC_ROUTES)

    def test_the_guide_covers_the_three_steps(self):
        import io as _io
        page = _io.open(os.path.join(os.path.dirname(PAGE), "kokoro.html"),
                        encoding="utf-8").read()
        self.assertIn("docker run", page)
        self.assertIn("reverse_proxy", page)
        self.assertIn("Caddyfile", page)
        # and the part only we can tell them: where it goes in fireside
        self.assertIn("Account → Voice", page)
        # no leftover template tokens: this page is served by _legal(), which
        # only fills company and contact
        self.assertNotIn("__HOST_VERSION__", page)
