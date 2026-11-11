"""The replica has to stay awake while a render still has work.

A render is a thread in the hosted process, and the hosted process is allowed
to sleep (minReplicas=0). The platform does exactly that: on 11-11 the system
log shows `KEDAScaleTargetDeactivated 1->0` at 21:26:15, and the boot after it
marked Cat's three queued books "stalled". While a render is live the keeper
asks the app's own public address for /health, which is the traffic the scaler
counts.

The first version of this keeper asked the database instead, and on the live
box it never pinged once (the request metric stayed at the two health probes a
minute). So the tests here pin two things: the decision is the in-process count,
and the count really moves while a render is running.
"""

import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from host import jobs, keepwarm
from host.contracts import Audio, Book, Chapter, Quote, Report
from host.db import connect
from host import library
from storage.local import LocalBackend


class _Counter(BaseHTTPRequestHandler):
    hits = []

    def do_GET(self):
        _Counter.hits.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *a):
        pass


def _wait_for(fn, timeout=5.0, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(step)
    return False


class _BlockingSpeech:
    """A speech server that holds the render open until the test lets it go."""
    id = "slow"
    ext = "mp3"
    mime = "audio/mpeg"
    needs_key = False

    def __init__(self):
        self.gate = threading.Event()
        self.started = threading.Event()

    def synth(self, text, voice, opts, cred):
        self.started.set()
        self.gate.wait(10)
        return Audio(data=b"tone", ext="mp3", mime="audio/mpeg")

    def list_voices(self, cred):
        return []

    def quote(self, ctx):
        return Quote(price=0.0, charge="free")

    def report(self, ctx):
        return Report(charge="free")


class KeepWarmTest(unittest.TestCase):

    def setUp(self):
        jobs.SYNTH_BACKOFF = 0.0
        self.dir = tempfile.mkdtemp(prefix="fireside-keepwarm-")
        _Counter.hits = []
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Counter)
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        keepwarm._started = None

    def test_tick_pings_only_with_a_live_render(self):
        self.assertFalse(keepwarm.tick(self.base), "no render, no request")
        self.assertEqual(_Counter.hits, [])

        jobs._active_enter()
        try:
            self.assertTrue(keepwarm.tick(self.base))
            self.assertEqual(_Counter.hits, ["/health"])
        finally:
            jobs._active_leave()

        self.assertFalse(keepwarm.tick(self.base))
        self.assertEqual(_Counter.hits, ["/health"], "a finished render must not ping")

    def test_a_real_render_moves_the_count(self):
        storage = LocalBackend(os.path.join(self.dir, "blobs"))
        conn = connect(":memory:")
        library.create_user(conn, "u1")
        book = Book(title="B", chapters=[Chapter("c0", "aa")])
        bid = library.add_book(conn, storage, "u1", book)
        speech = _BlockingSpeech()
        self.assertEqual(jobs.active_renders(), 0)
        t = threading.Thread(target=lambda: jobs.render_book(
            conn, storage, speech, "u1", bid, voice="v1"))
        t.start()
        try:
            self.assertTrue(speech.started.wait(10), "the render never started")
            self.assertEqual(jobs.active_renders(), 1,
                             "a render in flight must be visible to the keeper")
            # the keeper's own decision, taken while the render is blocked
            self.assertTrue(keepwarm.tick(self.base))
        finally:
            speech.gate.set()
            t.join(15)
        self.assertEqual(jobs.active_renders(), 0)
        self.assertFalse(keepwarm.tick(self.base))

    def test_a_failed_ping_raises_and_the_thread_survives_it(self):
        jobs._active_enter()
        try:
            with self.assertRaises(Exception):
                keepwarm.tick("http://127.0.0.1:9")     # nothing listens there
        finally:
            jobs._active_leave()
        # The loop catches it and keeps asking; a dead address must not end it.
        jobs._active_enter()
        try:
            th = keepwarm.start(base_url="http://127.0.0.1:9", interval=0.1)
            self.assertIsNotNone(th)
            time.sleep(0.4)
            self.assertTrue(th.is_alive(), "one failed ping killed the keeper")
        finally:
            jobs._active_leave()

    def test_no_public_address_means_no_thread(self):
        self.assertIsNone(keepwarm.start(base_url=""))
        self.assertIsNone(keepwarm.start(base_url="localhost:8888"))


if __name__ == "__main__":
    unittest.main()