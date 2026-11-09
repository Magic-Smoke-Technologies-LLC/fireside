"""Analytics: absent by default, cookieless until a reader asks for more.

Three things are easy to get wrong here and none of them raise:

  * shipping a tag on a self-hosted copy because the snippet was pasted rather
    than rendered from the operator's own setting;
  * writing the consent default after gtag.js has loaded, which means it never
    applied and the first pageview is collected with cookies anyway;
  * naming the wrong payment processor in the documents a processor's reviewer
    reads, which is what the checkout page did until this change.

So the tests read the served bytes: the id comes from the environment, the
consent default is in the head before any loader exists, and the public pages
name the processor that actually runs the payments.
"""

import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

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


class AnalyticsCase(unittest.TestCase):
    ga_id = None

    def setUp(self):
        self._saved = os.environ.get("FIRESIDE_GA_ID")
        os.environ.pop("FIRESIDE_GA_ID", None)
        if self.ga_id:
            os.environ["FIRESIDE_GA_ID"] = self.ga_id
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
        if self._saved is None:
            os.environ.pop("FIRESIDE_GA_ID", None)
        else:
            os.environ["FIRESIDE_GA_ID"] = self._saved

    def page(self, path="/"):
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (self.port, path)) as r:
            return r.read().decode("utf-8")


class TestNoIdShipsNoTag(AnalyticsCase):

    def test_the_page_carries_an_empty_id(self):
        html = self.page()
        self.assertIn('window.FS_GA = ""', html)
        self.assertNotIn("__GA_ID__", html)

    def test_the_consent_default_is_in_the_head_anyway(self):
        # Cheap, and it is what makes the FIRST pageview cookieless on the day
        # an id is switched on. A default that has to be added later is a
        # default that gets forgotten.
        html = self.page()
        self.assertIn("gtag('consent', 'default'", html)


class TestIdFromTheEnvironment(AnalyticsCase):
    ga_id = "G-TEST12345"

    def test_the_render_carries_the_operators_id(self):
        html = self.page()
        self.assertIn('window.FS_GA = "G-TEST12345"', html)
        self.assertNotIn("__GA_ID__", html)

    def test_the_consent_default_comes_before_any_loader(self):
        # Source order, not block order: the same lesson as the theme cascade.
        html = self.page()
        default = html.index("gtag('consent', 'default'")
        loader = html.index("googletagmanager.com/gtag/js")
        self.assertLess(default, loader,
                        "a consent default written after the loader never applied")
        self.assertIn("analytics_storage: 'denied'", html)

    def test_a_junk_id_is_truncated_not_trusted(self):
        os.environ["FIRESIDE_GA_ID"] = "G-" + "9" * 60
        html = self.page()
        self.assertIn('window.FS_GA = "G-%s"' % ("9" * 30), html)
        self.assertNotIn("9" * 31, html)


class TestTheDocumentsNameTheRealProcessor(AnalyticsCase):
    """The checkout page named Creem while the live store charges through
    Paddle. A processor's reviewer reading that page is reading the wrong
    merchant of record."""

    def test_checkout_names_paddle(self):
        html = self.page("/checkout")
        self.assertIn("Paddle", html)
        self.assertNotIn("Creem", html)

    def test_terms_names_paddle(self):
        html = self.page("/terms")
        self.assertIn("Paddle", html)
        self.assertNotIn("Creem", html)

    def test_privacy_names_paddle_and_says_what_analytics_does(self):
        html = self.page("/privacy")
        self.assertIn("Paddle", html)
        self.assertNotIn("Creem", html)
        self.assertIn("Google Analytics", html)
        self.assertIn("cookieless", html)


if __name__ == "__main__":
    unittest.main()