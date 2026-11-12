"""The checkout page: a real price and a real pay button when a provider is on.

Two failure modes this page has already had, both invisible to a green suite:

  * the page quoted the launch ladder ($3/$8/$20 a month) long after the ladder
    changed, because the numbers were typed into the HTML instead of read from
    the catalog;
  * the pay button was inert on every deployment, including the one a payment
    processor's reviewer reads.

So the tests read the served bytes: prices come from the catalog, the button's
state follows whether a provider is configured, and the browser gets a client
token and price ids but never the API key.
"""

import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from host import payments
from host import payments_paddle as paddle
from host.app import App, make_handler
from tests.test_pluginhost import write_plugin

READER_BODY = """
import types
class Plugin:
    def matches(self, req):
        return req.get("kind") == "upload"
    def read(self, req, fetch, cred):
        return types.SimpleNamespace(title="T", meta={}, id=None, chapters=[])
    def settings(self):
        return []
    def credential_hosts(self):
        return []
"""

STORAGE_BODY = """
class Plugin:
    def __init__(self):
        self._d = {}
    def put(self, data):
        import hashlib
        r = hashlib.sha256(data).hexdigest(); self._d[r] = data; return r
    def has(self, ref):
        return ref in self._d
    def get(self, ref):
        return self._d[ref]
    def delete(self, ref):
        self._d.pop(ref, None)
    def info(self):
        return {"kind": "memory", "durable": False, "url_ttl": None}
"""

ENV_KEYS = ("FIRESIDE_PADDLE_API_KEY", "FIRESIDE_PADDLE_WEBHOOK_SECRET",
            "FIRESIDE_PADDLE_CLIENT_TOKEN", "FIRESIDE_PADDLE_ENV",
            "FIRESIDE_PADDLE_PRICES", "FIRESIDE_PADDLE_PLAN_PRICES",
            "FIRESIDE_PAYMENT_PROVIDER")

API_KEY = "pdl_live_apikey_should_never_ship_to_a_browser"


class CheckoutCase(unittest.TestCase):
    env = {}

    def setUp(self):
        self._saved_env = {k: os.environ.get(k) for k in ENV_KEYS}
        self._saved_registry = dict(payments.REGISTRY)
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        for k, v in self.env.items():
            os.environ[k] = v
        if self.env.get("FIRESIDE_PADDLE_API_KEY"):
            payments.register(paddle.from_env())
        self.tmp = tempfile.mkdtemp()
        root = os.path.join(self.tmp, "plugins")
        write_plugin(root, "reader", "fake", order=10, body=READER_BODY)
        write_plugin(root, "storage", "mem", body=STORAGE_BODY)
        self.app = App(root, db_path=os.path.join(self.tmp, "db.sqlite"),
                       storage_id="mem", require_auth=True)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        payments.REGISTRY.clear()
        payments.REGISTRY.update(self._saved_registry)

    def page(self, path="/checkout"):
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (self.port, path)) as r:
            return r.read().decode("utf-8")


class TestNoProvider(CheckoutCase):

    def test_the_page_says_payment_is_not_open_and_the_button_is_inert(self):
        html = self.page()
        self.assertIn('"configured":false', html)
        self.assertIn("Pay (disabled)", html)
        self.assertIn("This store is not accepting payments yet.", html)

    def test_the_prices_are_the_catalog_not_the_launch_ladder(self):
        # $5/$10/$22 a month; $36/$96/$240 a year. The old page typed
        # $3/$8/$20 into the HTML and kept saying it after the change.
        html = self.page()
        self.assertIn('"month_cents":500', html)
        self.assertIn('"year_cents":3600', html)
        self.assertIn('"year_cents":24000', html)
        self.assertNotIn("$30.00 a year", html)

    def test_the_page_still_names_paddle_as_merchant_of_record(self):
        html = self.page()
        self.assertIn("Paddle", html)


class TestSandboxProviderOn(CheckoutCase):
    env = {"FIRESIDE_PADDLE_API_KEY": "pdl_sdbx_apikey_test",
           "FIRESIDE_PADDLE_WEBHOOK_SECRET": "pdl_ntfset_test",
           "FIRESIDE_PADDLE_CLIENT_TOKEN": "test_client_token",
           "FIRESIDE_PADDLE_ENV": "sandbox"}

    def test_the_page_carries_the_client_token_and_the_price_ids(self):
        html = self.page()
        self.assertIn('"configured":true', html)
        self.assertIn("test_client_token", html)
        self.assertIn(paddle.DEFAULT_PLAN_PRICES["platform"]["month"], html)

    def test_the_page_has_no_inert_link_for_a_button(self):
        # The old page shipped <a id="pay" onclick="return false">: a button
        # that could never do anything on any deployment.
        html = self.page()
        self.assertNotIn("onclick=\"return false\"", html)
        self.assertIn('id="pay"', html)

    def test_the_api_key_never_reaches_the_page(self):
        os.environ["FIRESIDE_PADDLE_API_KEY"] = API_KEY
        payments.REGISTRY.clear()
        payments.register(paddle.from_env())
        html = self.page()
        self.assertNotIn(API_KEY, html)
        self.assertNotIn("apikey", html)


class TestTheBudgetQuotedIsTheOneCharged(CheckoutCase):
    """A production catalog replaces the sandbox ids wholesale. If the page
    kept printing sandbox prices next to live ones, the buyer would be quoted a
    number nobody charges."""

    env = {"FIRESIDE_PADDLE_API_KEY": "pdl_live_apikey_x",
           "FIRESIDE_PADDLE_WEBHOOK_SECRET": "pdl_ntfset_live",
           "FIRESIDE_PADDLE_CLIENT_TOKEN": "live_client_token",
           "FIRESIDE_PADDLE_ENV": "production",
           "FIRESIDE_PADDLE_PRICES":
               '{"pri_live_reader_m": "platform", "pri_live_reader_y": "platform"}',
           "FIRESIDE_PADDLE_PLAN_PRICES":
               '{"platform": {"month": "pri_live_reader_m", "year": "pri_live_reader_y"}}'}

    def test_live_ids_are_what_the_page_hands_the_browser(self):
        html = self.page()
        self.assertIn("pri_live_reader_m", html)
        self.assertIn("pri_live_reader_y", html)
        self.assertNotIn(paddle.DEFAULT_PLAN_PRICES["platform"]["month"], html)
        self.assertIn('"env":"production"', html)


if __name__ == "__main__":
    unittest.main()