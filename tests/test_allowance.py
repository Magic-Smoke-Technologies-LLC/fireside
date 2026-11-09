"""The monthly grant a paid plan carries.

Checks the things that make it safe: it lands once per period, it does not land
for a tier that is not paid, a reload cannot stack it, and the unit follows the
account (dollars for a dollar wallet, characters for an account that still holds
characters).
"""

import time
import unittest

from host import accounting, allowance, billing, quota
from host.db import connect


class AllowanceTest(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")
        self.uid = "u1"
        self.conn.execute("INSERT INTO users(id,name,created_at) VALUES(?,?,?)",
                          (self.uid, "test", "0"))
        self.conn.commit()

    def _sub(self, plan=quota.PLATFORM_PLAN, status="active", days=20):
        end = int(time.time()) + days * 86400
        return billing.upsert(self.conn, self.uid, plan, status,
                              current_period_end=end)

    def test_grants_once_per_period(self):
        self._sub()
        first = allowance.due(self.conn, self.uid)
        self.assertEqual(first, quota.PLATFORM_MONTH_GRANT)
        self.assertEqual(accounting.balance(self.conn, self.uid),
                         quota.PLATFORM_MONTH_GRANT)
        self.assertEqual(accounting.currency(self.conn, self.uid), "USD")
        # a reload is not a second grant
        self.assertEqual(allowance.due(self.conn, self.uid), 0)
        self.assertEqual(accounting.balance(self.conn, self.uid),
                         quota.PLATFORM_MONTH_GRANT)

    def test_new_period_grants_again(self):
        self._sub(days=20)
        allowance.due(self.conn, self.uid)
        # the provider's renewal moves the period end
        self._sub(days=50)
        self.assertEqual(allowance.due(self.conn, self.uid),
                         quota.PLATFORM_MONTH_GRANT)
        self.assertEqual(accounting.balance(self.conn, self.uid),
                         2 * quota.PLATFORM_MONTH_GRANT)

    def test_grant_is_dollars_for_a_dollar_wallet_and_chars_for_a_chars_wallet(self):
        # The cutover is per account: an old account must not be handed dollars
        # it cannot spend on a character meter, and a new one must not be handed
        # characters it has no meter for.
        self._sub()
        allowance.due(self.conn, self.uid)
        self.assertEqual(accounting.balance(self.conn, self.uid), 1.0)
        self.assertEqual(accounting.currency(self.conn, self.uid), "USD")

        old = "u_old"
        self.conn.execute("INSERT INTO users(id,name,created_at) VALUES(?,?,?)",
                          (old, "old", "0"))
        self.conn.commit()
        accounting.topup(self.conn, old, 1000, currency="chars")
        billing.upsert(self.conn, old, quota.PLATFORM_PLAN, "active",
                       current_period_end=int(time.time()) + 20 * 86400)
        self.assertEqual(allowance.due(self.conn, old), quota.PLATFORM_MONTH_CHARS)
        self.assertEqual(accounting.balance(self.conn, old),
                         1000 + quota.PLATFORM_MONTH_CHARS)
        self.assertEqual(accounting.currency(self.conn, old), "chars")

    def test_studio_gets_fifteen(self):
        self._sub(plan=quota.STUDIO_PLAN)
        self.assertEqual(allowance.due(self.conn, self.uid), 15.0)

    def test_free_tier_gets_nothing(self):
        self._sub(plan=quota.FREE_PLAN)
        self.assertEqual(allowance.due(self.conn, self.uid), 0)
        self.assertEqual(accounting.balance(self.conn, self.uid), 0.0)

    def test_lapsed_period_gets_nothing(self):
        self._sub(status="active", days=20)
        # a period that ended well past the grace window is not active even
        # though the row still says active
        self.conn.execute("UPDATE subscriptions SET current_period_end=? WHERE user_id=?",
                          (int(time.time()) - 10 * 86400, self.uid))
        self.conn.commit()
        self.assertFalse(billing.active(self.conn, self.uid))
        self.assertEqual(allowance.due(self.conn, self.uid), 0)

    def test_lapsed_past_due(self):
        # revoked by the provider
        self._sub(status="canceled")
        self.assertEqual(allowance.due(self.conn, self.uid), 0)

    def test_no_subscription(self):
        self.assertEqual(allowance.due(self.conn, self.uid), 0)

    def test_unknown_user_is_harmless(self):
        self.assertEqual(allowance.due(self.conn, "nobody"), 0)


class WelcomeGrantTest(unittest.TestCase):
    """The $3 welcome credit: once in the life of the account, and only after
    the first real charge. A trial is not a charge, which is the whole reason
    the gate is status "active" and not billing.active()."""

    def setUp(self):
        self.conn = connect(":memory:")
        self.uid = "u1"
        self.conn.execute("INSERT INTO users(id,name,created_at) VALUES(?,?,?)",
                          (self.uid, "test", "0"))
        self.conn.commit()

    def _sub(self, plan=quota.PLATFORM_PLAN, status="active", days=20):
        end = int(time.time()) + days * 86400
        return billing.upsert(self.conn, self.uid, plan, status,
                              current_period_end=end)

    def test_free_account_gets_nothing(self):
        self._sub(plan=quota.FREE_PLAN)
        self.assertEqual(allowance.welcome(self.conn, self.uid), 0)
        self.assertEqual(accounting.balance(self.conn, self.uid), 0.0)

    def test_a_trial_is_not_a_charge(self):
        self._sub(status="trialing")
        self.assertEqual(allowance.welcome(self.conn, self.uid), 0)
        self.assertEqual(accounting.balance(self.conn, self.uid), 0.0)
        # the trial still carries the plan's included credit, as it always did
        self.assertEqual(allowance.due(self.conn, self.uid),
                         quota.PLATFORM_MONTH_GRANT)
        self.assertEqual(accounting.balance(self.conn, self.uid),
                         quota.PLATFORM_MONTH_GRANT)

    def test_the_first_paid_month_gets_it_once(self):
        self._sub(status="trialing")
        allowance.welcome(self.conn, self.uid)
        # the trial converts: the provider says active on the same period
        self._sub(status="active")
        self.assertEqual(allowance.welcome(self.conn, self.uid),
                         quota.WELCOME_GRANT)
        self.assertEqual(allowance.welcome(self.conn, self.uid), 0)
        allowance.due(self.conn, self.uid)
        self.assertEqual(accounting.balance(self.conn, self.uid),
                         quota.WELCOME_GRANT + quota.PLATFORM_MONTH_GRANT)

    def test_a_cancel_and_resubscribe_cannot_recollect_it(self):
        self._sub(days=20)
        allowance.welcome(self.conn, self.uid)
        self._sub(status="canceled", days=20)
        self.assertEqual(allowance.welcome(self.conn, self.uid), 0)
        self._sub(status="active", days=60)
        self.assertEqual(allowance.welcome(self.conn, self.uid), 0)

    def test_a_character_wallet_never_gets_dollars(self):
        other = "u_old"
        self.conn.execute("INSERT INTO users(id,name,created_at) VALUES(?,?,?)",
                          (other, "old", "0"))
        self.conn.commit()
        accounting.topup(self.conn, other, 1000, currency="chars")
        billing.upsert(self.conn, other, quota.PLATFORM_PLAN, "active",
                       current_period_end=int(time.time()) + 20 * 86400)
        self.assertEqual(allowance.welcome(self.conn, other), 0)
        self.assertEqual(accounting.currency(self.conn, other), "chars")

    def test_unknown_user_is_harmless(self):
        self.assertEqual(allowance.welcome(self.conn, "nobody"), 0)
        self.assertEqual(allowance.welcome(self.conn, ""), 0)


if __name__ == "__main__":
    unittest.main()
