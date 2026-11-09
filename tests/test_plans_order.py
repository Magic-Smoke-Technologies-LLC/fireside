"""What the price list says first.

Chris, 11-09: "On the plans page I feel like 'On every plan' and 'Run it
yourself, free' should also be promoted up in the page."

The order is the argument the page makes. Read top to bottom it should be:
what the thing is, what every plan includes, the prices, then the free
do-it-yourself path, then the narration rates. Rungs first would make the page
a price table with the offer buried under it.

A source-order test, not a screenshot: the cards are replaced by the live
catalog at load, but the section order around them is static, and that is the
thing being fixed.
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


class TestPlansOrder(unittest.TestCase):

    def test_every_plan_comes_before_the_rungs(self):
        body = plans_section()
        every = body.index('data-t="On every plan"')
        grid = body.index('id="pageplans"')
        self.assertLess(every, grid,
                        "what every plan includes should not be buried under the prices")

    def test_run_it_yourself_comes_before_the_narration_rates(self):
        body = plans_section()
        self_ = body.index('data-t="Run it yourself, free"')
        rates = body.index('data-t="Narration, bought on its own"')
        self.assertLess(self_, rates)

    def test_the_rungs_still_come_before_the_narration_rates(self):
        # Promoted, not shuffled: the price ladder is still the middle of the page
        body = plans_section()
        self.assertLess(body.index('id="pageplans"'),
                        body.index('data-t="Narration, bought on its own"'))

    def test_the_wallet_promise_still_sits_above_the_prices(self):
        body = plans_section()
        self.assertLess(body.index('id="walletpromise"'), body.index('id="pageplans"'))


if __name__ == "__main__":
    unittest.main()
