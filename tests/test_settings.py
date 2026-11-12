"""Operator settings: the admin screen that turns the card provider on.

The point of the screen is that a saved value is used, and that a wrong value is
refused before it can look saved. So these tests are about three things: the
batch is validated as a whole (a live key against the sandbox is the failure
this exists for), the secret never comes back to the page, and clearing a
setting beats the environment it came from.
"""

import json
import os
import tempfile
import unittest

from host import payments, settings
from host.app import App
from tests.test_app import READER_BODY, STORAGE_BODY
from tests.test_pluginhost import write_plugin

SPEECH_BODY = """
from host.contracts import Audio, Quote, Report
class Plugin:
    id = "fake"
    ext = "mp3"
    mime = "audio/mpeg"
    needs_key = False
    def settings(self):
        return []
    def list_voices(self, cred):
        return [{"id": "v", "label": "V"}]
    def synth(self, text, voice, opts, cred):
        return Audio(data=("A:" + text).encode())
    def cost_per_1m_chars(self, cred):
        return 0.0
    def quote(self, ctx):
        return Quote(chars_est=ctx.get("chars_est", 0), price=0.0)
    def report(self, ctx):
        return Report(charge="charged", cost=0.0, currency="USD")
"""

LIVE_KEY = "pdl_live_apikey_01abcdefghijklmnop"
SANDBOX_KEY = "pdl_sdbx_apikey_01abcdefghijklmnop"
PRICES = {"platform": {"month": "pri_aaa", "year": "pri_bbb"},
          "listener": {"month": "pri_ccc", "year": "pri_ddd"}}
ENV_VARS = ("FIRESIDE_PADDLE_ENV", "FIRESIDE_PADDLE_API_KEY",
            "FIRESIDE_PADDLE_CLIENT_TOKEN", "FIRESIDE_PADDLE_WEBHOOK_SECRET",
            "FIRESIDE_PADDLE_PLAN_PRICES", "FIRESIDE_PADDLE_PRICES",
            "FIRESIDE_PAYMENT_PROVIDER")


class TestSettingsStore(unittest.TestCase):
    def setUp(self):
        self.saved_env = {k: os.environ.get(k) for k in ENV_VARS}
        for k in ENV_VARS:
            os.environ.pop(k, None)
        self.app = make_app(self)
        self.conn = self.app.conn

    def tearDown(self):
        for k, v in self.saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        payments.REGISTRY.clear()

    def test_a_live_key_against_the_sandbox_is_refused_whole(self):
        saved, errors = settings.save(self.conn, {
            "paddle_env": "sandbox", "paddle_api_key": LIVE_KEY})
        self.assertEqual(saved, [])
        self.assertIn("paddle_env", errors)
        # nothing was written: a refused batch leaves the last good one alone
        self.assertEqual(settings.stored(self.conn, "paddle_api_key"), None)

    def test_the_api_key_prefix_is_checked(self):
        _, errors = settings.save(self.conn, {"paddle_api_key": "hello"})
        self.assertIn("pdl_live_apikey_", errors["paddle_api_key"])

    def test_a_blank_env_is_read_off_the_key(self):
        saved, errors = settings.save(self.conn, {"paddle_api_key": LIVE_KEY})
        self.assertEqual(errors, {})
        self.assertEqual(settings.effective(self.conn, "paddle_env"), "production")

    def test_a_test_token_against_production_is_refused(self):
        _, errors = settings.save(self.conn, {
            "paddle_env": "production", "paddle_api_key": LIVE_KEY,
            "paddle_client_token": "test_abcd"})
        self.assertIn("paddle_client_token", errors)

    def test_prices_need_a_monthly_id_and_one_tier_per_price(self):
        _, errors = settings.save(self.conn, {"paddle_plan_prices": {
            "platform": {"year": "pri_bbb"}}})
        self.assertIn("monthly", errors["paddle_plan_prices"])
        _, errors = settings.save(self.conn, {"paddle_plan_prices": {
            "platform": {"month": "pri_aaa", "year": "pri_aaa"},
            "listener": {"month": "pri_aaa", "year": "pri_ddd"}}})
        self.assertIn("both", errors["paddle_plan_prices"])
        _, errors = settings.save(self.conn, {"paddle_plan_prices": {
            "platform": {"month": "aaa", "year": "pri_bbb"}}})
        self.assertIn("not a price id", errors["paddle_plan_prices"])

    def test_apply_writes_the_env_and_derives_the_webhook_table(self):
        settings.save(self.conn, {"paddle_api_key": SANDBOX_KEY,
                                  "paddle_webhook_secret": "pdl_ntfset_xyz",
                                  "paddle_client_token": "test_abcd",
                                  "paddle_plan_prices": PRICES})
        settings.apply(self.conn)
        self.assertEqual(os.environ["FIRESIDE_PADDLE_API_KEY"], SANDBOX_KEY)
        self.assertEqual(os.environ["FIRESIDE_PADDLE_ENV"], "sandbox")
        # one catalog, two shapes: the reverse map comes from the page's table
        by_id = json.loads(os.environ["FIRESIDE_PADDLE_PRICES"])
        self.assertEqual(by_id["pri_aaa"], "platform")
        self.assertEqual(by_id["pri_ddd"], "listener")

    def test_clearing_a_setting_beats_an_environment_value(self):
        os.environ["FIRESIDE_PADDLE_WEBHOOK_SECRET"] = "from-the-deploy"
        self.assertEqual(settings.effective(self.conn, "paddle_webhook_secret"),
                         "from-the-deploy")
        settings.save(self.conn, {}, clear=["paddle_webhook_secret"])
        self.assertEqual(settings.effective(self.conn, "paddle_webhook_secret"), "")
        settings.apply(self.conn)
        self.assertNotIn("FIRESIDE_PADDLE_WEBHOOK_SECRET", os.environ)

    def test_the_screen_never_gets_a_secret_back(self):
        settings.save(self.conn, {"paddle_api_key": SANDBOX_KEY,
                                  "paddle_webhook_secret": "pdl_ntfset_secret9"})
        v = settings.view(self.conn)
        api = next(f for f in v["fields"] if f["key"] == "paddle_api_key")
        self.assertEqual(api["value"], "")
        self.assertTrue(api["set"])
        self.assertEqual(api["hint"], SANDBOX_KEY[-4:])
        blob = json.dumps(v)
        self.assertNotIn(SANDBOX_KEY, blob)
        self.assertNotIn("pdl_ntfset_secret9", blob)

    def test_ready_says_what_is_still_missing(self):
        settings.save(self.conn, {"paddle_api_key": SANDBOX_KEY,
                                  "paddle_webhook_secret": "s"})
        v = settings.view(self.conn)
        self.assertFalse(v["ready"])
        self.assertIn("paddle_client_token", v["missing"])
        self.assertIn("paddle_plan_prices", v["missing"])

    def test_a_saved_key_registers_the_provider_without_a_restart(self):
        os.environ["FIRESIDE_PAYMENT_PROVIDER"] = "host.payments_paddle"
        self.assertFalse(payments.configured())
        s, out = self.app.admin_billing_save({"_user": "u_boss"}, {"values": {
            "paddle_api_key": SANDBOX_KEY,
            "paddle_webhook_secret": "pdl_ntfset_secret",
            "paddle_client_token": "test_abcd",
            "paddle_plan_prices": PRICES}})
        self.assertEqual(s, 200, out)
        self.assertTrue(payments.configured())
        self.assertEqual(payments.get().api_key, SANDBOX_KEY)
        # the page's half agrees with the server's now
        s, conf = self.app.billing_config({}, {})
        self.assertTrue(conf["ok"])
        self.assertEqual(conf["env"], "sandbox")
        self.assertEqual(conf["prices"]["platform"]["year"], "pri_bbb")

    def test_only_an_admin_reads_or_writes_it(self):
        self.assertEqual(self.app.admin_billing({"_user": "u_reader"}, {})[0], 403)
        self.assertEqual(self.app.admin_billing_save(
            {"_user": "u_reader"}, {"values": {"paddle_api_key": SANDBOX_KEY}})[0], 403)
        self.assertEqual(self.app.admin_billing_check({"_user": "u_reader"}, {})[0], 403)

    def test_a_bad_batch_returns_the_field_and_the_unchanged_view(self):
        s, out = self.app.admin_billing_save({"_user": "u_boss"}, {"values": {
            "paddle_env": "production", "paddle_api_key": SANDBOX_KEY}})
        self.assertEqual(s, 400)
        self.assertEqual(out["error"], "not saved")
        self.assertIn("paddle_env", out["fields"])
        self.assertIn("settings", out)

    def test_check_names_the_price_that_is_not_in_the_catalog(self):
        settings.save(self.conn, {"paddle_api_key": SANDBOX_KEY,
                                  "paddle_webhook_secret": "s",
                                  "paddle_plan_prices": PRICES})

        def call(method, url, key, payload=None, timeout=15):
            self.assertIn("sandbox-api", url)
            return {"data": [{"id": "pri_aaa"}, {"id": "pri_bbb"},
                             {"id": "pri_ccc"}],
                    "meta": {"pagination": {"next": ""}}}, ""

        out = settings.check(self.conn, call=call)
        self.assertFalse(out["ok"])
        self.assertEqual(out["env"], "sandbox")
        by_id = {c["id"]: c for c in out["checks"]}
        self.assertTrue(by_id["key"]["ok"])
        self.assertFalse(by_id["catalog"]["ok"])
        self.assertIn("pri_ddd", by_id["catalog"]["detail"])

    def test_check_says_the_webhook_cannot_be_proven_without_an_event(self):
        settings.save(self.conn, {"paddle_api_key": SANDBOX_KEY,
                                  "paddle_webhook_secret": "s",
                                  "paddle_plan_prices": {"platform": {
                                      "month": "pri_aaa", "year": "pri_bbb"}}})

        def call(method, url, key, payload=None, timeout=15):
            return {"data": [{"id": "pri_aaa"}, {"id": "pri_bbb"}],
                    "meta": {"pagination": {"next": ""}}}, ""

        out = settings.check(self.conn, call=call)
        self.assertTrue(out["ok"])
        webhook = next(c for c in out["checks"] if c["id"] == "webhook")
        self.assertIn("only proof", webhook["detail"])

    def test_a_bad_key_is_reported_not_swallowed(self):
        settings.save(self.conn, {"paddle_api_key": SANDBOX_KEY,
                                  "paddle_webhook_secret": "s"})

        def call(method, url, key, payload=None, timeout=15):
            return None, "HTTP 403 nope"

        out = settings.check(self.conn, call=call)
        self.assertFalse(out["ok"])
        self.assertIn("403", {c["id"]: c["detail"] for c in out["checks"]}["key"])


def make_app(case):
    tmp = tempfile.mkdtemp()
    root = os.path.join(tmp, "plugins")
    write_plugin(root, "reader", "fake", order=10, body=READER_BODY)
    write_plugin(root, "speech", "fake", body=SPEECH_BODY)
    write_plugin(root, "storage", "mem", body=STORAGE_BODY)
    app = App(root, db_path=os.path.join(tmp, "db.sqlite"), storage_id="mem")
    app.conn.execute("INSERT INTO users(id,name,created_at) VALUES(?,?,?)",
                     ("u_boss", "u_boss", "2026-01-01T00:00:00Z"))
    app.conn.execute("INSERT INTO users(id,name,created_at) VALUES(?,?,?)",
                     ("u_reader", "u_reader", "2026-01-01T00:00:00Z"))
    app.conn.execute("INSERT INTO admins(user_id,created_at) VALUES(?,?)",
                     ("u_boss", "2026-01-01"))
    return app


if __name__ == "__main__":
    unittest.main()