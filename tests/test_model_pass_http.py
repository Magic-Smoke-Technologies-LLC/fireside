"""The model pass over the real routes: a tick at import, suggestions on screen.

Proves the wiring a reader actually touches (11-11): the import checkbox decides
which books the model may read, the answers arrive as suggestions that change no
audio, accepting them is the only thing that makes them rules, and the page can
say which chapters predate the change when the reader comes back.
"""

import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
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
        return types.SimpleNamespace(title="T", meta={}, id=None,
            chapters=[types.SimpleNamespace(title="c0", text="We live here."),
                      types.SimpleNamespace(title="c1", text="A tear in the page.")])
    def settings(self):
        return []
    def credential_hosts(self):
        return []
"""

SPEECH_BODY = """
from host.contracts import Audio, Quote, Report
class Plugin:
    id = "fake"
    ext = "mp3"
    mime = "audio/mpeg"
    needs_key = False
    def list_voices(self, cred):
        return [{"id": "vv", "label": "Voice V"}]
    def synth(self, text, voice, opts, cred):
        return Audio(data=("A:" + voice + ":" + text).encode())
    def quote(self, ctx):
        return Quote(chars_est=ctx.get("chars_est", 0), price=0.0)
    def report(self, ctx):
        return Report(charge="charged", cost=0.0, currency="USD")
"""

STORAGE_BODY = """
import hashlib
class Plugin:
    def __init__(self):
        self._d = {}
    def put(self, data):
        r = hashlib.sha256(data).hexdigest()
        self._d[r] = data
        return r
    def has(self, ref):
        return ref in self._d
    def get(self, ref):
        return self._d[ref]
    def delete(self, ref):
        self._d.pop(ref, None)
    def info(self):
        return {"kind": "memory", "durable": False, "url_ttl": None}
"""

# Counts its own calls so a test can prove an un-ticked import never reaches it.
DISAMBIG_BODY = """
class Plugin:
    CALLS = []
    def decide(self, text, model, cred):
        Plugin.CALLS.append(text)
        return [{"word": "live", "say": "liv", "why": "we live here"}]
    def scan(self, text, model, cred):
        return []
    def list_models(self, cred):
        return []
    def quote(self, ctx):
        return None
    def settings(self):
        return []
"""


class TestModelPassHttp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        root = os.path.join(self.tmp, "plugins")
        write_plugin(root, "reader", "fake", order=10, body=READER_BODY)
        write_plugin(root, "speech", "fake", body=SPEECH_BODY)
        write_plugin(root, "storage", "mem", body=STORAGE_BODY)
        write_plugin(root, "disambig", "fake", body=DISAMBIG_BODY)
        self.env = {"FIRESIDE_DISAMBIG_URL": "http://box:11434/v1",
                    "FIRESIDE_DISAMBIG_MODEL": "tiny",
                    "FIRESIDE_DISAMBIG_ACCOUNTS": ""}
        self.old_env = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        self.app = App(root, db_path=os.path.join(self.tmp, "db.sqlite"),
                       storage_id="mem")
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        for k, v in self.old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _post(self, path, obj):
        req = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, path),
            data=json.dumps(obj).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())

    def _jget(self, path):
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (self.port, path)) as r:
            return r.status, json.loads(r.read())

    def _wait_pass(self, bid, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            s, st = self._jget("/book/model?user=u1&book=" + bid)
            if st.get("status") in ("suggested", "accepted", "discarded", "failed"):
                return st
            time.sleep(0.05)
        self.fail("the pass on %s never finished" % bid)

    def _import(self, **extra):
        body = {"user": "u1", "kind": "upload", "filename": "x.epub"}
        body.update(extra)
        s, imp = self._post("/import", body)
        self.assertEqual(s, 200, imp)
        return imp

    def calls(self):
        return self.app.host.disambig["fake"].obj.CALLS

    def _render(self, bid):
        s, r = self._post("/render", {"user": "u1", "book": bid,
                                      "provider": "fake", "voice": "v"})
        self.assertEqual(s, 200, r)
        end = time.time() + 10
        while time.time() < end:
            _, job = self._jget("/job?id=" + r["job_id"])
            if job["status"] in ("done", "error"):
                return job
            time.sleep(0.05)
        self.fail("render did not finish")

    def test_the_box_is_offered_only_when_a_model_box_is_set(self):
        s, plugs = self._jget("/plugins")
        self.assertTrue(plugs["model_pass"])

    def test_a_ticked_import_is_analyzed_and_the_answers_are_suggestions(self):
        imp = self._import(model_pass=True)
        self.assertTrue(imp["model_pass"])
        st = self._wait_pass(imp["book_id"])
        self.assertEqual(st["status"], "suggested")
        self.assertEqual([r["from"] for r in st["rules"]], ["live"])
        self.assertEqual(st["words"], 1)
        # the reader's own words are untouched: a guess is not their setting
        s, words = self._jget("/pron?user=u1")
        self.assertEqual(words["rules"], [])

    def test_an_unticked_import_never_calls_the_model(self):
        imp = self._import()
        self.assertFalse(imp["model_pass"])
        s, st = self._jget("/book/model?user=u1&book=" + imp["book_id"])
        self.assertEqual(st["status"], "")
        self.assertEqual(st["words"], 0)
        self._render(imp["book_id"])
        self.assertEqual(self.calls(), [])

    def test_accepting_is_what_changes_the_audio_and_the_page_says_what_is_stale(self):
        imp = self._import(model_pass=True)
        bid = imp["book_id"]
        self._wait_pass(bid)
        # voice it under the reader's words first
        self._render(bid)
        s, before = self._jget("/book/model?user=u1&book=" + bid)
        self.assertEqual(before["stale"]["chapters"], 0)
        s, r = self._post("/book/model", {"user": "u1", "book": bid,
                                          "action": "apply"})
        self.assertEqual(s, 200, r)
        self.assertEqual(r["status"], "accepted")
        self.assertEqual(r["words"], 1)
        s, after = self._jget("/book/model?user=u1&book=" + bid)
        # the one chapter that says "live" is now out of date, and the page can
        # name it without the reader doing arithmetic
        self.assertEqual(after["stale"]["chapters"], 1)

    def test_discard_leaves_nothing_of_the_model_in_the_audio(self):
        imp = self._import(model_pass=True)
        bid = imp["book_id"]
        self._wait_pass(bid)
        self._post("/book/model", {"user": "u1", "book": bid, "action": "apply"})
        s, r = self._post("/book/model", {"user": "u1", "book": bid,
                                          "action": "discard"})
        self.assertEqual(r["status"], "discarded")
        self._render(bid)
        s, a = self._get_bytes("/chapter?book=%s&idx=0" % bid)
        self.assertEqual(a, b"A:v:We live here.")

    def _get_bytes(self, path):
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (self.port, path)) as r:
            return r.status, r.read()

    def test_a_second_account_cannot_ask_about_someone_elses_book(self):
        imp = self._import()
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._jget("/book/model?user=u2&book=" + imp["book_id"])
        self.assertEqual(cm.exception.code, 403)

    def test_scan_is_the_readers_own_button(self):
        imp = self._import()
        bid = imp["book_id"]
        s, r = self._post("/book/model", {"user": "u1", "book": bid, "action": "scan"})
        self.assertEqual(s, 200, r)
        st = self._wait_pass(bid)
        self.assertEqual(st["status"], "suggested")
        self.assertEqual(st["words"], 1)

