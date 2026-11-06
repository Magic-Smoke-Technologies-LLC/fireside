"""The platform tier's monthly character allowance.

Checks the three things that make it safe: it lands once per period, it does not
land for a tier that is not paid, and a reload cannot stack it.
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
        self.assertEqual(first, quota.PLATFORM_MONTH_CHARS)
        self.assertEqual(accounting.balance(self.conn, self.uid),
                         quota.PLATFORM_MONTH_CHARS)
        # a reload is not a second grant
        self.assertEqual(allowance.due(self.conn, self.uid), 0)
        self.assertEqual(accounting.balance(self.conn, self.uid),
                         quota.PLATFORM_MONTH_CHARS)

    def test_new_period_grants_again(self):
        self._sub(days=20)
        allowance.due(self.conn, self.uid)
        # the provider's renewal moves the period end
        self._sub(days=50)
        self.assertEqual(allowance.due(self.conn, self.uid),
                         quota.PLATFORM_MONTH_CHARS)
        self.assertEqual(accounting.balance(self.conn, self.uid),
                         2 * quota.PLATFORM_MONTH_CHARS)

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


if __name__ == "__main__":
    unittest.main()
