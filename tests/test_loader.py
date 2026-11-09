"""The boot mark: a file, not inline SVG, and public.

It is served as a document of its own because its stylesheet uses bare class
names (.s, .icon, .fire). Pasted into the page those rules would land in the
global cascade and start styling every other svg on the site, and the mark's
prefers-reduced-motion query would stop applying.

Public because it is the first thing a signed-out visitor's page asks for.
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
    def matches(self, req): return req.get("kind") == "upload"
    def read(self, req, fetch, cred):
        return types.SimpleNamespace(title="T", meta={}, id=None, chapters=[])
    def settings(self): return []
    def credential_hosts(self): return []
"""

STORAGE_BODY = """
class Plugin:
    def __init__(self): self._d = {}
    def put(self, data):
        import hashlib
        r = hashlib.sha256(data).hexdigest(); self._d[r] = data; return r
    def has(self, ref): return ref in self._d
    def get(self, ref): return self._d[ref]
    def delete(self, ref): self._d.pop(ref, None)
    def info(self): return {"kind": "memory", "durable": False, "url_ttl": None}
"""


class TestLoader(unittest.TestCase):

    def setUp(self):
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

    def get(self, path):
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (self.port, path)) as r:
            return r.status, r.headers.get("Content-Type"), r.read()

    def test_it_serves_without_a_session(self):
        # require_auth is on, and the first paint still needs it
        s, ct, body = self.get("/loader.svg")
        self.assertEqual(s, 200)
        self.assertIn("image/svg+xml", ct)
        self.assertTrue(body.lstrip().startswith(b"<svg"))

    def test_it_carries_its_own_reduced_motion_rule(self):
        _s, _ct, body = self.get("/loader.svg")
        text = body.decode("utf-8")
        self.assertIn("prefers-reduced-motion", text)

    def test_the_page_points_at_the_file_not_an_inline_copy(self):
        s, _ct, body = self.get("/")
        page = body.decode("utf-8")
        self.assertIn('id="bootload"', page)
        self.assertIn('src="/loader.svg"', page)
        # an inlined copy would put these bare selectors in the page cascade
        self.assertNotIn('class="icon fire s"', page)


if __name__ == "__main__":
    unittest.main()
