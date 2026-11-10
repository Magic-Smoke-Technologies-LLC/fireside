"""Plans: the free sign-up default, paid storage blocks, and the admin comp.

The policy is two tiers and one add-on. These check that a new account starts
free, that an account with no plan row the host does not know fails open (never
locked out), that a paid block adds its bytes, and that an admin can put an
account on a tier without a payment. Prices come from the provider, never here.
"""

import unittest

from host import auth, billing, payments, plans, quota
from host.app import App
from host.db import connect, now
from host.payments import Checkout, Plan, Receipt, Subscription


class TestPolicy(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")

    def _user(self, uid):
        self.conn.execute("INSERT OR IGNORE INTO users(id,name,created_at) VALUES(?,?,?)",
                          (uid, uid, now()))
        self.conn.commit()

    def test_new_account_is_on_free(self):
        uid = auth.local_register(self.conn, "alice", "password123")
        self.assertEqual(billing.get(self.conn, uid)["plan"], quota.FREE_PLAN)
        self.assertEqual(quota.allowance_bytes(billing.get(self.conn, uid)["plan"]),
                         quota.FREE_ALLOWANCE_BYTES)

    def test_account_without_a_row_fails_open_to_platform(self):
        # an account created before the free tier existed has no plan row; it must
        # not be silently cut down to 500 MB
        self._user("legacy")
        self.assertIsNone(billing.get(self.conn, "legacy"))
        self.assertEqual(quota.allowance_bytes(""), quota.PLATFORM_ALLOWANCE_BYTES)

    def test_storage_blocks_add_their_bytes(self):
        self.assertEqual(quota.allowance_bytes("free", 2),
                         quota.FREE_ALLOWANCE_BYTES + 2 * quota.STORAGE_BLOCK_BYTES)
        self.assertEqual(quota.allowance_bytes("platform", 1),
                         quota.PLATFORM_ALLOWANCE_BYTES + quota.STORAGE_BLOCK_BYTES)

    def test_blocks_count_only_when_active(self):
        self._user("u1")
        self.assertEqual(billing.blocks(self.conn, "u1"), 0)
        billing.upsert_addon(self.conn, "u1", "storage", 3, "active", provider="stripe")
        self.assertEqual(billing.blocks(self.conn, "u1"), 3)
        billing.upsert_addon(self.conn, "u1", "storage", 3, "canceled", provider="stripe")
        self.assertEqual(billing.blocks(self.conn, "u1"), 0)

    def test_catalog_without_a_provider_states_no_price(self):
        cat = plans.catalog(None)
        self.assertFalse(cat["payments_configured"])
        # the ladder is free plus three paid rungs (2026-11-06)
        self.assertEqual([t["id"] for t in cat["tiers"]], list(quota.TIERS))
        self.assertEqual(cat["tiers"][0]["included_bytes"], quota.FREE_ALLOWANCE_BYTES)
        self.assertEqual([t["included_bytes"] for t in cat["tiers"][1:]],
                         [quota.PLATFORM_ALLOWANCE_BYTES,
                          quota.LISTENER_ALLOWANCE_BYTES,
                          quota.STUDIO_ALLOWANCE_BYTES])
        # the grant is dollars into the wallet (2026-11-09), not a character
        # allowance: $1 / $3 / $15, and it rolls instead of expiring
        self.assertEqual([t["included_usd"] for t in cat["tiers"]],
                         [0.0, 1.0, 3.0, 15.0])
        for t in cat["tiers"][1:]:
            self.assertIsNone(t["amount_cents"])
        # our published list price rides along even with no provider, so a card
        # can print what the rung costs instead of "price not set"; the charged
        # amount stays None until a provider supplies it.
        self.assertEqual([t["list_amount_cents"] for t in cat["tiers"]],
                         [None, 500, 1000, 2200])
        self.assertEqual([t["list_annual_cents"] for t in cat["tiers"]],
                         [None, 3600, 9600, 24000])
        self.assertEqual(cat["storage"]["block_bytes"], quota.STORAGE_BLOCK_BYTES)
        self.assertIsNone(cat["storage"]["amount_cents"])

    def test_catalog_takes_prices_from_the_provider(self):
        class P:
            def plans(self):
                return [Plan("platform", "x", 300, "usd", "month", ""),
                        Plan("storage", "y", 100, "usd", "month", "")]
        cat = plans.catalog(P())
        self.assertTrue(cat["payments_configured"])
        plat = [t for t in cat["tiers"] if t["id"] == "platform"][0]
        self.assertEqual(plat["amount_cents"], 300)
        # a provider price wins over the list; the list is still reported, so a
        # screen that shows "our price" cannot silently drift from the charge
        self.assertEqual(plat["list_amount_cents"], 500)
        self.assertEqual(cat["storage"]["amount_cents"], 100)


class FakeProvider:
    id = "fake"

    def charge(self, usd, meta=None):
        return Receipt(True, "pi_1", "ok")

    def plans(self):
        return [Plan("platform", "fireside platform", 300, "usd", "month", "x"),
                Plan("storage", "fireside storage +5 GB", 100, "usd", "month", "y")]

    def checkout(self, plan_id, meta=None):
        if plan_id not in ("platform", "storage"):
            return Checkout(False, detail="unknown plan")
        return Checkout(True, "https://pay.test/x", "cs_1")

    def confirm(self, ref):
        if ref == "cs_plat":
            return Subscription(True, "u1", "platform", "active", 2000000000, ref, "ok")
        if ref == "cs_store":
            return Subscription(True, "u1", "storage", "active", 2000000000, ref,
                                "ok", quantity=3)
        return Subscription(False, detail="not found")


class TestRoutes(unittest.TestCase):
    def setUp(self):
        payments.REGISTRY.clear()
        payments.register(FakeProvider())
        self.app = App(db_path=":memory:")
        self.app.conn.execute("INSERT OR IGNORE INTO users(id,name,created_at) VALUES(?,?,?)",
                              ("admin1", "admin1", now()))
        self.app.conn.execute("INSERT OR IGNORE INTO users(id,name,created_at) VALUES(?,?,?)",
                              ("u1", "u1", now()))
        self.app.conn.execute("INSERT OR IGNORE INTO admins(user_id,created_at) VALUES(?,?)",
                              ("admin1", now()))
        self.app.conn.commit()

    def tearDown(self):
        payments.REGISTRY.clear()

    def test_plans_route_lists_tiers_and_block(self):
        code, out = self.app.plans_get({}, {})
        self.assertEqual(code, 200)
        self.assertEqual(out["tiers"][0]["id"], "free")
        self.assertEqual(out["storage"]["amount_cents"], 100)

    def test_storage_route_reports_blocks(self):
        billing.upsert_addon(self.app.conn, "u1", "storage", 2, "active", provider="fake")
        code, out = self.app.storage_get({"user": "u1"}, {})
        self.assertEqual(code, 200)
        self.assertEqual(out["storage_blocks"], 2)
        self.assertEqual(out["allowance_bytes"],
                         quota.PLATFORM_ALLOWANCE_BYTES + 2 * quota.STORAGE_BLOCK_BYTES)

    def test_a_free_account_cannot_buy_storage_blocks(self):
        # Storage is what a plan buys. The wallet pays for narration on free;
        # room on the shelf is not for sale there (Chris, 2026-11-09).
        self.app.plans_admin_set({"_user": "admin1"}, {"user": "u1", "plan": "free"})
        code, out = self.app.billing_checkout({"user": "u1"},
                                              {"plan": "storage", "quantity": 1})
        self.assertEqual(code, 402)
        self.assertIn("plan", out["error"])
        # a tier is still for sale, and a cart that names a tier may carry blocks,
        # because that buyer is upgrading in the same purchase
        code, _ = self.app.billing_checkout({"user": "u1"}, {"plan": "platform"})
        self.assertEqual(code, 200)
        code, _ = self.app.billing_checkout({"user": "u1"}, {"items": [
            {"plan": "platform", "quantity": 1},
            {"plan": "storage", "quantity": 1}]})
        self.assertEqual(code, 200)
        # an account with no plan row fails open to platform, and keeps its
        # ability to buy blocks (the legacy accounts before the free tier)
        code, _ = self.app.billing_checkout({"user": "admin1"},
                                            {"plan": "storage", "quantity": 1})
        self.assertEqual(code, 200)

    def test_storage_checkout_confirm_lands_as_an_addon_not_a_tier(self):
        code, out = self.app.billing_checkout({"user": "u1"}, {"plan": "storage",
                                                               "quantity": 3})
        self.assertEqual(code, 200)
        code, out = self.app.billing_confirm({"user": "u1"}, {"reference": "cs_store"})
        self.assertEqual(code, 200)
        self.assertIn("addon", out)
        self.assertEqual(out["addon"]["quantity"], 3)
        self.assertEqual(billing.blocks(self.app.conn, "u1"), 3)
        # the tier is untouched - a storage buy does not move you off platform/free
        self.assertIsNone(billing.get(self.app.conn, "u1"))

    def test_platform_confirm_still_writes_the_tier(self):
        code, out = self.app.billing_confirm({"user": "u1"}, {"reference": "cs_plat"})
        self.assertEqual(code, 200)
        self.assertEqual(out["subscription"]["plan"], "platform")

    def test_admin_can_comp_a_plan(self):
        code, out = self.app.plans_admin_set({"_user": "admin1"},
                                             {"user": "u1", "plan": "platform"})
        self.assertEqual(code, 200)
        self.assertEqual(billing.get(self.app.conn, "u1")["plan"], "platform")
        self.assertEqual(billing.get(self.app.conn, "u1")["provider"], "admin")

    def test_admin_list_shows_each_account_and_plan(self):
        self.app.plans_admin_set({"_user": "admin1"}, {"user": "u1", "plan": "free"})
        code, out = self.app.plans_admin({"_user": "admin1"}, {})
        self.assertEqual(code, 200)
        by_id = {u["user_id"]: u for u in out["users"]}
        self.assertEqual(by_id["u1"]["plan"], "free")

    def test_non_admin_is_refused(self):
        code, _ = self.app.plans_admin_set({"_user": "u1"}, {"user": "u2",
                                                             "plan": "platform"})
        self.assertEqual(code, 403)

    def test_bad_plan_is_refused(self):
        code, _ = self.app.plans_admin_set({"_user": "admin1"},
                                           {"user": "u1", "plan": "gold"})
        self.assertEqual(code, 400)


if __name__ == "__main__":
    unittest.main()
