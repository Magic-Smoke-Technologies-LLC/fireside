"""Paddle: signature, event mirror, access, and the portal mint.

The point of these tests is the seam, not the shape: a forged delivery must
write nothing, a replayed one must write nothing twice, an out-of-order one must
not overwrite a newer truth, and a first-ever subscription for a buyer we have
never seen must still find its account.
"""

import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest

from host import billing, payments
from host.db import connect
from host import payments_paddle as paddle
from host.payments_paddle import Paddle, normalize_event, verify_signature

SECRET = "pdl_ntfset_test_3f9a"
READER_MONTH = "pri_01m6szmwtw6dt4fgk5e24cdmaf"
READER_YEAR = "pri_01m6szmwx3174hsp6qe85yest5"
STUDIO_MONTH = "pri_01m6szmx8beq24b3v32rnm999q"


def sign(body, ts=None, secret=SECRET):
    ts = int(time.time()) if ts is None else int(ts)
    raw = body if isinstance(body, bytes) else body.encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"),
                      str(ts).encode("ascii") + b":" + raw,
                      hashlib.sha256).hexdigest()
    return "ts=%d;h1=%s" % (ts, digest)


def sub_event(event_type, status="active", price=READER_MONTH, sid="sub_1",
              uid="user_1", cid="ctm_1", ends="2026-12-07T12:00:00Z",
              occurred="2026-11-07T12:00:00Z", change=None, eid=None):
    data = {"id": sid, "customer_id": cid, "status": status,
            "items": [{"status": status, "price": {"id": price},
                       "product_id": "pro_1"}],
            "current_billing_period": {"ends_at": ends},
            "custom_data": {paddle.METADATA_KEY: uid} if uid else {}}
    if change:
        data["scheduled_change"] = change
    return {"event_id": eid or "ntf_%s_%s" % (event_type, status),
            "event_type": event_type, "occurred_at": occurred, "data": data}


class TestSignature(unittest.TestCase):
    body = json.dumps({"event_id": "ntf_1", "event_type": "customer.created",
                       "data": {"id": "ctm_1"}}, separators=(",", ":"))

    def test_genuine_delivery_verifies(self):
        self.assertIsNone(verify_signature(self.body, sign(self.body), SECRET))

    def test_tampered_body_fails(self):
        header = sign(self.body)
        tampered = self.body.replace("ctm_1", "ctm_2")
        self.assertIn("does not match", verify_signature(tampered, header, SECRET))

    def test_wrong_secret_fails(self):
        header = sign(self.body, secret="pdl_ntfset_other")
        self.assertIn("does not match", verify_signature(self.body, header, SECRET))

    def test_no_secret_configured_is_an_error_not_a_pass(self):
        self.assertIn("no webhook secret", verify_signature(self.body, sign(self.body), ""))

    def test_stale_timestamp_fails(self):
        old = int(time.time()) - 60
        why = verify_signature(self.body, sign(self.body, ts=old), SECRET)
        self.assertIn("outside", why)

    def test_window_can_be_widened_and_disabled(self):
        old = int(time.time()) - 120
        header = sign(self.body, ts=old)
        self.assertIsNone(verify_signature(self.body, header, SECRET, tolerance=300))
        self.assertIsNone(verify_signature(self.body, header, SECRET, tolerance=0))

    def test_rotation_accepts_any_matching_h1(self):
        ts = int(time.time())
        good = sign(self.body, ts=ts)
        h1 = good.split("h1=")[1]
        self.assertIsNone(verify_signature(self.body, "ts=%d;h1=deadbeef;h1=%s" % (ts, h1), SECRET))

    def test_missing_header_fails_with_a_reason(self):
        self.assertIn("no ts or h1", verify_signature(self.body, "", SECRET))

    def test_raw_bytes_are_what_is_signed(self):
        """A body re-serialized by a JSON parser no longer matches: the route
        must hand over the bytes it read, not a dict."""
        parsed = json.loads(self.body)
        reserialized = json.dumps(parsed, indent=1)
        header = sign(self.body)
        self.assertIsNotNone(verify_signature(reserialized, header, SECRET))


class TestNormalize(unittest.TestCase):
    def test_subscription_event_carries_tier_and_epoch(self):
        ev = normalize_event(sub_event("subscription.created"))
        self.assertEqual(ev["kind"], "subscription")
        sub = ev["subscription"]
        self.assertEqual(sub["plan"], "platform")
        self.assertEqual(sub["status"], "active")
        self.assertEqual(sub["current_period_end"], 1796644800)
        self.assertEqual(sub["user_id"], "user_1")
        self.assertEqual(ev["customer"]["customer_id"], "ctm_1")

    def test_annual_price_maps_to_the_same_tier(self):
        ev = normalize_event(sub_event("subscription.created", price=READER_YEAR))
        self.assertEqual(ev["subscription"]["plan"], "platform")

    def test_unknown_price_never_guesses_a_tier(self):
        ev = normalize_event(sub_event("subscription.created", price="pri_unknown"))
        self.assertEqual(ev["subscription"]["plan"], "")

    def test_billing_item_wins_over_a_canceled_one(self):
        data = {"id": "sub_9", "customer_id": "ctm_1", "status": "active",
                "items": [{"status": "canceled", "price": {"id": "pri_old"}},
                          {"status": "active", "price": {"id": STUDIO_MONTH}}],
                "current_billing_period": {"ends_at": "2026-12-01T00:00:00Z"}}
        ev = normalize_event({"event_type": "subscription.updated", "data": data})
        self.assertEqual(ev["subscription"]["price_id"], STUDIO_MONTH)
        self.assertEqual(ev["subscription"]["plan"], "studio")

    def test_scheduled_change_is_recorded_not_applied(self):
        ev = normalize_event(sub_event("subscription.updated",
                                       change={"action": "cancel",
                                               "effective_at": "2026-12-07T12:00:00Z"}))
        sub = ev["subscription"]
        self.assertEqual(sub["scheduled_change_action"], "cancel")
        self.assertEqual(sub["scheduled_change_at"], 1796644800)
        self.assertEqual(sub["status"], "active")

    def test_customer_event_kind(self):
        ev = normalize_event({"event_type": "customer.updated",
                              "data": {"id": "ctm_1", "email": "a@b.c",
                                       "custom_data": {"fireside_user": "user_9"}}})
        self.assertEqual(ev["kind"], "customer")
        self.assertEqual(ev["customer"]["email"], "a@b.c")
        self.assertEqual(ev["customer"]["user_id"], "user_9")

    def test_unhandled_type_is_ignored_not_guessed(self):
        ev = normalize_event({"event_type": "report.created", "data": {}})
        self.assertEqual(ev["kind"], "ignore")


class TestMirror(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.conn = connect(os.path.join(self.tmp, "t.db"))

    def tearDown(self):
        self.conn.close()

    def apply(self, event):
        return billing.apply_event(self.conn, normalize_event(event))

    def test_first_event_from_an_unknown_buyer_links_and_grants(self):
        """No row existed before this webhook. The custom_data id is what finds
        the account; the tier then appears where quota/allowance already read."""
        out = self.apply(sub_event("subscription.created"))
        self.assertTrue(out["applied"])
        self.assertEqual(out["user"], "user_1")
        row = billing.get(self.conn, "user_1")
        self.assertEqual(row["plan"], "platform")
        self.assertEqual(row["provider"], "paddle")
        self.assertEqual(row["subscription_id"], "sub_1")
        self.assertEqual(row["customer"], "ctm_1")
        self.assertTrue(billing.active(self.conn, "user_1"))
        self.assertEqual(billing.customer_for(self.conn, "user_1")["customer_id"], "ctm_1")

    def test_email_is_the_fallback_link(self):
        self.conn.execute("INSERT INTO local_accounts (username, user_id, digest, "
                          "salt, email, created_at) VALUES (?,?,?,?,?,?)",
                          ("cat", "local:cat", "x", "y", "cat@example.com", "t"))
        self.conn.commit()
        # Paddle sends customer.created before subscription.created, so the
        # address is what links a buyer who signed up with a different provider
        # and carries no account id in custom_data.
        self.apply({"event_type": "customer.created",
                    "event_id": "ntf_cust",
                    "data": {"id": "ctm_2", "email": "cat@example.com"}})
        out = self.apply(sub_event("subscription.created", uid=None, sid="sub_2",
                                   cid="ctm_2"))
        self.assertEqual(out["user"], "local:cat")
        self.assertTrue(billing.active(self.conn, "local:cat"))

    def test_replay_of_the_same_event_writes_nothing_twice(self):
        ev = sub_event("subscription.created")
        first = self.apply(ev)
        second = self.apply(ev)
        self.assertTrue(first["applied"])
        self.assertFalse(second["applied"])
        self.assertEqual(second["detail"], "already applied")
        n = self.conn.execute("SELECT COUNT(*) AS n FROM paddle_events").fetchone()["n"]
        self.assertEqual(n, 1)

    def test_out_of_order_delivery_does_not_rewind(self):
        self.apply(sub_event("subscription.updated", status="active",
                             occurred="2026-11-07T12:00:00Z", eid="ntf_new"))
        self.apply(sub_event("subscription.created", status="trialing",
                             occurred="2026-11-01T12:00:00Z", eid="ntf_old"))
        row = billing.get(self.conn, "user_1")
        self.assertEqual(row["status"], "active")
        self.assertTrue(billing.active(self.conn, "user_1"))

    def test_newer_trialing_wins_over_older_active(self):
        self.apply(sub_event("subscription.created", status="active",
                             occurred="2026-11-01T12:00:00Z", eid="ntf_a"))
        self.apply(sub_event("subscription.updated", status="trialing",
                             occurred="2026-11-07T12:00:00Z", eid="ntf_b"))
        self.assertEqual(billing.get(self.conn, "user_1")["status"], "trialing")
        self.assertTrue(billing.active(self.conn, "user_1"))

    def test_cancel_revokes_access(self):
        self.apply(sub_event("subscription.created"))
        self.apply(sub_event("subscription.canceled", status="canceled",
                             occurred="2026-12-07T12:00:00Z", eid="ntf_c"))
        self.assertFalse(billing.active(self.conn, "user_1"))

    def test_pending_cancel_keeps_access_until_it_lands(self):
        self.apply(sub_event("subscription.created"))
        self.apply(sub_event("subscription.updated", status="active",
                             change={"action": "cancel",
                                     "effective_at": "2026-12-07T12:00:00Z"},
                             occurred="2026-11-20T12:00:00Z", eid="ntf_d"))
        row = billing.get(self.conn, "user_1")
        self.assertEqual(row["status"], "active")
        self.assertTrue(billing.active(self.conn, "user_1"))
        mirrored = billing.subscription_rows(self.conn, "user_1")[0]
        self.assertEqual(mirrored["scheduled_change_action"], "cancel")
        # The account screen has to be able to say "stops on" rather than
        # "renews on", and status is active for both, so the date is the only
        # way to tell them apart.
        self.assertEqual(row["cancel_at"], 1796644800)

    def test_a_resumed_subscription_clears_the_scheduled_cancel(self):
        self.apply(sub_event("subscription.created"))
        self.apply(sub_event("subscription.updated", status="active",
                             change={"action": "cancel",
                                     "effective_at": "2026-12-07T12:00:00Z"},
                             occurred="2026-11-20T12:00:00Z", eid="ntf_r1"))
        self.assertTrue(billing.get(self.conn, "user_1")["cancel_at"])
        self.apply(sub_event("subscription.updated", status="active",
                             occurred="2026-11-21T12:00:00Z", eid="ntf_r2"))
        self.assertEqual(billing.get(self.conn, "user_1")["cancel_at"], 0)

    def test_past_due_does_not_grant(self):
        self.apply(sub_event("subscription.created"))
        self.apply(sub_event("subscription.past_due", status="past_due",
                             occurred="2026-12-08T12:00:00Z", eid="ntf_e"))
        self.assertFalse(billing.active(self.conn, "user_1"))

    def test_upgrade_projects_the_higher_tier(self):
        self.apply(sub_event("subscription.created", sid="sub_a", eid="ntf_1",
                             ends="2026-12-07T12:00:00Z"))
        self.apply(sub_event("subscription.created", sid="sub_b", eid="ntf_2",
                             price=STUDIO_MONTH, occurred="2026-11-08T12:00:00Z",
                             ends="2026-12-08T12:00:00Z"))
        row = billing.get(self.conn, "user_1")
        self.assertEqual(row["plan"], "studio")
        self.assertEqual(row["subscription_id"], "sub_b")

    def test_canceling_the_old_rung_does_not_remove_the_new_one(self):
        self.apply(sub_event("subscription.created", sid="sub_a", eid="ntf_1",
                             ends="2026-12-07T12:00:00Z"))
        self.apply(sub_event("subscription.created", sid="sub_b", eid="ntf_2",
                             price=STUDIO_MONTH, occurred="2026-11-08T12:00:00Z",
                             ends="2026-12-08T12:00:00Z"))
        self.apply(sub_event("subscription.canceled", sid="sub_a", status="canceled",
                             eid="ntf_3", occurred="2026-11-09T12:00:00Z"))
        row = billing.get(self.conn, "user_1")
        self.assertEqual(row["plan"], "studio")
        self.assertTrue(billing.active(self.conn, "user_1"))

    def test_unknown_price_changes_nothing(self):
        self.apply(sub_event("subscription.created", sid="sub_a", eid="ntf_1"))
        before = billing.get(self.conn, "user_1")
        self.apply(sub_event("subscription.created", sid="sub_x", eid="ntf_2",
                             price="pri_who_knows"))
        self.assertEqual(billing.get(self.conn, "user_1")["plan"], before["plan"])

    def test_admin_grant_outranks_a_sale(self):
        billing.upsert(self.conn, "user_1", "studio", "active", provider="admin")
        self.apply(sub_event("subscription.created"))
        self.assertEqual(billing.get(self.conn, "user_1")["plan"], "studio")

    def test_grants_access_matches_active(self):
        statuses = ("active", "trialing", "past_due", "paused", "canceled", "", None)
        for s in statuses:
            with self.subTest(status=s):
                self.assertEqual(billing.grants_access(s),
                                 str(s or "").strip().lower() in ("active", "trialing"))

    def test_expired_period_lapses_without_a_webhook(self):
        """A delivery that never arrives must not keep a paid tier running."""
        self.apply(sub_event("subscription.created", ends="2020-01-01T00:00:00Z"))
        self.assertFalse(billing.active(self.conn, "user_1"))


class TestProvider(unittest.TestCase):
    def test_half_configured_still_registers_so_failures_are_loud(self):
        os.environ["FIRESIDE_PADDLE_API_KEY"] = "pdl_sdbx_apikey_x"
        os.environ.pop("FIRESIDE_PADDLE_WEBHOOK_SECRET", None)
        try:
            p = paddle.from_env()
            self.assertIsNotNone(p)
            self.assertEqual(p.env, "sandbox")
            raw = b'{"event_type":"x"}'
            ev, err = p.verify_webhook(raw, "ts=1;h1=aa")
            self.assertIsNone(ev)
            self.assertIn("no webhook secret", err)
        finally:
            os.environ.pop("FIRESIDE_PADDLE_API_KEY", None)

    def test_nothing_configured_registers_nothing(self):
        for k in ("FIRESIDE_PADDLE_API_KEY", "FIRESIDE_PADDLE_WEBHOOK_SECRET"):
            os.environ.pop(k, None)
        self.assertIsNone(paddle.from_env())

    def test_checkout_points_at_the_overlay_page(self):
        co = Paddle("k", SECRET).checkout("platform", {"user": "u"})
        self.assertTrue(co.ok)
        self.assertEqual(co.url, "/pricing")

    def test_portal_without_a_key_says_so(self):
        res = Paddle("", SECRET).portal("ctm_1")
        self.assertFalse(res["ok"])
        self.assertIn("no Paddle API key", res["detail"])

    def test_portal_reads_the_overview_url(self):
        p = Paddle("key", SECRET)
        calls = []

        def fake(method, url, key, payload=None, timeout=15):
            calls.append((method, url, key, payload))
            return {"data": {"urls": {"general": {"overview": "https://portal/x"}}}}, ""

        orig = paddle._call
        paddle._call = fake
        try:
            res = p.portal("ctm_1", "/account")
        finally:
            paddle._call = orig
        self.assertTrue(res["ok"])
        self.assertEqual(res["url"], "https://portal/x")
        self.assertEqual(calls[0][0], "POST")
        self.assertIn("/customers/ctm_1/portal-sessions", calls[0][1])
        self.assertEqual(calls[0][2], "key")

    def test_cancel_asks_for_the_end_of_the_period(self):
        p = Paddle("key", SECRET)
        seen = {}

        def fake(method, url, key, payload=None, timeout=15):
            seen["url"] = url
            seen["payload"] = payload
            return {"data": {"scheduled_change": {"action": "cancel"}}}, ""

        orig = paddle._call
        paddle._call = fake
        try:
            res = p.cancel("sub_1")
        finally:
            paddle._call = orig
        self.assertTrue(res["ok"])
        self.assertEqual(seen["payload"]["effective_from"], "next_billing_period")
        self.assertTrue(seen["url"].endswith("/subscriptions/sub_1/cancel"))

    def test_cancel_needs_an_id(self):
        self.assertFalse(Paddle("key", SECRET).cancel("")["ok"])


class TestWebhookRoute(unittest.TestCase):
    """The route end to end: a forged body writes nothing, a genuine one grants."""

    def setUp(self):
        from host.app import App
        self.tmp = tempfile.mkdtemp()
        self.app = App(plugins_root=self.tmp, db_path=os.path.join(self.tmp, "t.db"))
        self.provider = Paddle("", SECRET, "sandbox")
        payments.REGISTRY.clear()
        payments.register(self.provider)

    def tearDown(self):
        payments.REGISTRY.clear()
        self.app.conn.close()

    def post(self, event, secret=SECRET):
        body = json.dumps(event, separators=(",", ":")).encode("utf-8")
        return self.app.payment_webhook(body, sign(body, secret=secret), "paddle")

    def test_forged_delivery_is_refused_and_writes_nothing(self):
        status, payload = self.post(sub_event("subscription.created"),
                                    secret="pdl_ntfset_wrong")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "bad webhook")
        self.assertIsNone(billing.get(self.app.conn, "user_1"))

    def test_genuine_delivery_grants_access(self):
        status, payload = self.post(sub_event("subscription.created"))
        self.assertEqual(status, 200)
        self.assertTrue(payload["applied"])
        self.assertTrue(billing.active(self.app.conn, "user_1"))

    def test_unknown_provider_is_404(self):
        status, _ = self.app.payment_webhook(b"{}", "ts=1;h1=x", "nope")
        self.assertEqual(status, 404)


class TestWebhookOverHTTP(unittest.TestCase):
    """The delivery as Paddle actually sends it: a POST with Paddle-Signature.

    The route-level tests above call the handler with a header string in hand.
    This one goes through the HTTP server, because the bug that would have cost
    the first sale lived there: the server read "Stripe-Signature" literally,
    so a genuine Paddle delivery arrived with no signature it recognised.
    """

    def setUp(self):
        import threading
        from http.server import ThreadingHTTPServer
        from host.app import App, make_handler
        self.tmp = tempfile.mkdtemp()
        self.app = App(plugins_root=self.tmp,
                       db_path=os.path.join(self.tmp, "t.db"))
        payments.REGISTRY.clear()
        payments.register(Paddle("", SECRET, "sandbox"))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                         make_handler(self.app))
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        payments.REGISTRY.clear()
        self.app.conn.close()

    def post(self, body, header):
        import urllib.error
        import urllib.request
        req = urllib.request.Request(
            "http://127.0.0.1:%d/webhook/paddle" % self.port, data=body,
            headers={"Content-Type": "application/json",
                     "Paddle-Signature": header})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_genuine_paddle_delivery_grants_access(self):
        body = json.dumps(sub_event("subscription.created"),
                          separators=(",", ":")).encode("utf-8")
        status, payload = self.post(body, sign(body))
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["applied"])
        self.assertTrue(billing.active(self.app.conn, "user_1"))

    def _get(self, path):
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (self.port, path),
                                    timeout=20) as r:
            return r.status, json.loads(r.read())

    def test_config_route_is_readable_signed_out_and_says_no_without_a_token(self):
        os.environ.pop("FIRESIDE_PADDLE_CLIENT_TOKEN", None)
        status, conf = self._get("/billing/config")
        self.assertEqual(status, 200)
        self.assertFalse(conf["ok"])
        self.assertTrue(conf["payments_configured"])
        self.assertNotIn("client_token", conf)

    def test_config_route_hands_the_page_its_prices(self):
        os.environ["FIRESIDE_PADDLE_CLIENT_TOKEN"] = "test_abc123"
        try:
            status, conf = self._get("/billing/config")
        finally:
            os.environ.pop("FIRESIDE_PADDLE_CLIENT_TOKEN", None)
        self.assertEqual(status, 200)
        self.assertTrue(conf["ok"])
        self.assertEqual(conf["prices"]["platform"]["month"], READER_MONTH)

    def test_a_paddle_body_signed_against_a_different_secret_is_refused(self):
        body = json.dumps(sub_event("subscription.created"),
                          separators=(",", ":")).encode("utf-8")
        status, payload = self.post(body, sign(body, secret="pdl_ntfset_wrong"))
        self.assertEqual(status, 400, payload)

    def test_body_and_signature_must_match_byte_for_byte(self):
        body = json.dumps(sub_event("subscription.created"),
                          separators=(",", ":")).encode("utf-8")
        header = sign(body)
        tampered = body.replace(b"sub_1", b"sub_2")
        status, _ = self.post(tampered, header)
        self.assertEqual(status, 400)
        self.assertIsNone(billing.get(self.app.conn, "user_1"))


class TestPublicConfig(unittest.TestCase):
    """What the browser is allowed to know, and what it never is."""

    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in
                      ("FIRESIDE_PADDLE_CLIENT_TOKEN", "FIRESIDE_PADDLE_ENV",
                       "FIRESIDE_PADDLE_API_KEY")}
        for k in self.saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_no_token_is_not_ok_and_carries_no_token(self):
        conf = paddle.public_config()
        self.assertFalse(conf["ok"])
        self.assertNotIn("client_token", conf)
        self.assertIn("client token", conf["detail"])

    def test_token_is_exposed_with_its_prices(self):
        os.environ["FIRESIDE_PADDLE_CLIENT_TOKEN"] = "test_abc123"
        os.environ["FIRESIDE_PADDLE_API_KEY"] = "pdl_sdbx_apikey_secret"
        conf = paddle.public_config()
        self.assertTrue(conf["ok"])
        self.assertEqual(conf["client_token"], "test_abc123")
        self.assertEqual(conf["prices"]["platform"]["month"], READER_MONTH)
        self.assertEqual(conf["prices"]["platform"]["year"], READER_YEAR)
        # The API key can move money; it must never ride along.
        self.assertNotIn("pdl_sdbx_apikey_secret", json.dumps(conf))

    def test_token_decides_the_environment_when_unset(self):
        os.environ["FIRESIDE_PADDLE_CLIENT_TOKEN"] = "live_abc123"
        self.assertEqual(paddle.public_config()["env"], "production")
        os.environ["FIRESIDE_PADDLE_CLIENT_TOKEN"] = "test_abc123"
        self.assertEqual(paddle.public_config()["env"], "sandbox")

    def test_plan_table_cannot_drift_from_the_price_map(self):
        for tier, cycles in paddle.DEFAULT_PLAN_PRICES.items():
            for cycle, price in cycles.items():
                self.assertIn(price, paddle.DEFAULT_PRICES,
                              "%s/%s points at a price no tier claims" % (tier, cycle))
                self.assertEqual(paddle.DEFAULT_PRICES[price], tier)
                self.assertIn(cycle, ("month", "year"))

    def test_plan_prices_env_replaces_wholesale(self):
        os.environ["FIRESIDE_PADDLE_PLAN_PRICES"] = json.dumps(
            {"platform": {"month": "pri_live_m", "year": "pri_live_y"}})
        try:
            prices = paddle.known_plan_prices()
            self.assertEqual(prices, {"platform": {"month": "pri_live_m",
                                                   "year": "pri_live_y"}})
        finally:
            os.environ.pop("FIRESIDE_PADDLE_PLAN_PRICES", None)

    def test_unknown_cycle_is_not_a_price(self):
        self.assertNotIn("week", paddle.known_plan_prices()["platform"])


class TestPricingPageWiring(unittest.TestCase):
    """The served pricing page must actually hand off to Paddle's overlay.

    Found by loading the live page in a headless browser instead of reading the
    deploy receipt: /billing/config returned ok=true with a client token, and
    every button still said "Not for sale yet". Paddle.Initialize was given an
    "environment" option it does not accept, the call threw inside the promise
    callback, and the enable loop and the test-mode note after it never ran. A
    source check can only prove the page as shipped; the live proof is a
    headless load that sees the buttons enabled.

    The page is now ui/index.html: /pricing serves the app opened on its price
    view, so the wiring moved with the prices rather than staying on a second
    static page that had drifted from the app it sold.
    """

    def setUp(self):
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "..", "ui", "index.html"), encoding="utf-8") as fh:
            self.page = fh.read()
        self.init = self.page.split("Paddle.Initialize", 1)[-1].split("})", 1)[0]

    def test_initialize_is_not_given_an_environment(self):
        self.assertNotIn("environment:", self.init,
                         "Paddle.js rejects this option and the page goes dead")

    def test_initialize_carries_the_client_token(self):
        self.assertIn("cfg.client_token", self.init)

    def test_a_failed_initialize_still_tells_the_visitor(self):
        # A dead button with no reason is the failure this page must not have.
        self.assertIn("try {", self.page)
        self.assertIn("did not start", self.page)

    def test_buttons_are_disabled_until_a_price_exists(self):
        # The page sells a cycle only when it has a price id for that cycle;
        # otherwise it says so instead of opening an overlay that cannot charge.
        self.assertIn("Not for sale yet", self.page)
        self.assertIn("paddlePriceId(cfg, t.id, cycle)", self.page)

    def test_the_yearly_cycle_is_reachable(self):
        # It lived only on the static page, so the app could not sell a year.
        # The cycle is now a choice above one Subscribe button rather than a
        # second button under it; what has to hold is that the card can still
        # start a yearly purchase.
        self.assertIn("buyPlan(t.id, pick.c)", self.page)
        self.assertIn("c: 'year'", self.page)


class TestSignatureHeader(unittest.TestCase):
    """The webhook reads the header the provider actually sends.

    Reading one literal name (Stripe-Signature) meant every Paddle delivery
    arrived unsigned and was refused with a 400 on the first real sale.
    """

    def setUp(self):
        from host.app import signature_header
        self.header = signature_header

    def test_paddle_header(self):
        h = {"Paddle-Signature": "ts=1;h1=aa"}
        self.assertEqual(self.header(h, "paddle"), "ts=1;h1=aa")

    def test_stripe_header(self):
        h = {"Stripe-Signature": "t=1,v1=bb"}
        self.assertEqual(self.header(h, "stripe"), "t=1,v1=bb")

    def test_provider_id_capitalises_to_the_documented_name(self):
        self.assertEqual(self.header({"Paddle-Signature": "x"}, "PADDLE"), "x")

    def test_empty_is_empty_not_a_crash(self):
        self.assertEqual(self.header({}, "paddle"), "")
        self.assertEqual(self.header(None, "paddle"), "")


if __name__ == "__main__":
    unittest.main()
