"""The replica has to stay awake while a render still has work.

A render is a thread in the hosted process, and the hosted process is allowed
to sleep (minReplicas=0). When it slept, every queued conversion died: four
books queued, one voiced, three marked "stopped" (Cat, 11-11). The keeper asks
the app's own public address for /health while a job is queued or running, so
the platform sees traffic and leaves the replica up.

What this test proves and what it does not:
- proves the keeper pings while work exists and stops when it does not;
- does not prove the platform honours the pings, which only a live render with
  the tab closed can show. That is the check after deploy.
"""

import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from host import jobs, keepwarm
from host.db import connect


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


class KeepWarmTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="fireside-keepwarm-")
        self.path = os.path.join(self.dir, "fireside.db")
        self.conn = connect(self.path)
        _Counter.hits = []
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Counter)
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        try:
            self.conn.close()
        except Exception:
            pass

    def _job(self, status="queued"):
        return jobs.start_job(self.conn, "u1", "b1", "local", "voice-x",
                              total=2, status=status)

    def test_it_pings_while_a_job_is_queued_and_stops_after(self):
        self._job("queued")
        keepwarm.start(self.path, base_url=self.base, interval=0.15)
        self.assertTrue(_wait_for(lambda: len(_Counter.hits) >= 2),
                        "the keeper never asked for /health while a job was queued")
        self.assertEqual(_Counter.hits[0], "/health")

        self.conn.execute("UPDATE jobs SET status='done'")
        self.conn.commit()
        time.sleep(0.5)                       # let any ping already in flight land
        settled = len(_Counter.hits)
        time.sleep(0.8)
        self.assertEqual(len(_Counter.hits), settled,
                         "the keeper kept pinging with nothing left to render")

    def test_no_public_address_means_no_thread(self):
        self.assertIsNone(keepwarm.start(self.path, base_url=""))
        self.assertIsNone(keepwarm.start(self.path, base_url="localhost:8888"))

    def test_any_active_sees_queued_and_running(self):
        self.assertFalse(jobs.any_active(self.conn))
        self._job("queued")
        self.assertTrue(jobs.any_active(self.conn))
        self.conn.execute("UPDATE jobs SET status='running'")
        self.conn.commit()
        self.assertTrue(jobs.any_active(self.conn))
        self.conn.execute("UPDATE jobs SET status='stalled'")
        self.conn.commit()
        self.assertFalse(jobs.any_active(self.conn))


if __name__ == "__main__":
    unittest.main()