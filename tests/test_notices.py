"""The bell that outlives the tab.

docs/NOTICES.md: one event, one row, kept until it is read. These cover the row
store itself, the two routes that expose it, and the one wiring that matters:
a render that finished while nobody was looking leaves something behind.
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

from host import notices
from host.app import App, make_handler
from host.db import connect
from tests.test_app import READER_BODY, SPEECH_BODY, STORAGE_BODY
from tests.test_pluginhost import write_plugin


class TestNoticeStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.conn = connect(os.path.join(self.tmp, "db.sqlite"))

    def tearDown(self):
        self.conn.close()

    def test_one_row_one_event(self):
        notices.notify(self.conn, "u1", "job", "Moby Dick is ready to play.",
                       route="#/play/b1")
        rows = notices.list_for(self.conn, "u1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], "Moby Dick is ready to play.")
        self.assertEqual(rows[0]["route"], "#/play/b1")
        self.assertTrue(rows[0]["unread"])

    def test_nobody_to_tell_writes_nothing(self):
        self.assertEqual(notices.notify(self.conn, "", "job", "x"), "")
        self.assertEqual(notices.notify(self.conn, "u1", "job", "   "), "")
        self.assertEqual(notices.unread(self.conn, "u1"), 0)

    def test_a_key_makes_a_retry_the_same_event(self):
        a = notices.notify(self.conn, "u1", "job", "Ready.", key="run:abc")
        b = notices.notify(self.conn, "u1", "job", "Ready.", key="run:abc")
        self.assertEqual(a, b)
        self.assertEqual(notices.unread(self.conn, "u1"), 1)
        # once read, a later write with the same key is a new event, not silence
        notices.mark_read(self.conn, "u1", None)
        c = notices.notify(self.conn, "u1", "job", "Ready.", key="run:abc")
        self.assertNotEqual(c, a)
        self.assertEqual(notices.unread(self.conn, "u1"), 1)

    def test_newest_first(self):
        old = notices.notify(self.conn, "u1", "job", "first")
        self.conn.execute("UPDATE notifications SET created_at=? WHERE id=?",
                          ("2000-01-01T00:00:00Z", old))
        self.conn.commit()
        new = notices.notify(self.conn, "u1", "job", "second")
        rows = notices.list_for(self.conn, "u1")
        self.assertEqual([r["id"] for r in rows], [new, old])

    def test_mark_read_is_scoped_to_the_owner(self):
        mine = notices.notify(self.conn, "u1", "job", "mine")
        theirs = notices.notify(self.conn, "u2", "job", "theirs")
        left = notices.mark_read(self.conn, "u1", [mine, theirs])
        self.assertEqual(left, 0)
        self.assertEqual(notices.unread(self.conn, "u2"), 1)

    def test_unread_rows_survive_a_prune(self):
        for i in range(notices.KEEP + 12):
            notices.notify(self.conn, "u1", "job", "n%d" % i)
        self.assertEqual(notices.unread(self.conn, "u1"), notices.KEEP + 12)
        notices.mark_read(self.conn, "u1", None)
        notices.notify(self.conn, "u1", "job", "after")
        # the read rows are the ones that get dropped; an unread row is the
        # whole point of a notice, so it stays even past KEEP
        read_rows = self.conn.execute(
            "SELECT COUNT(*) AS n FROM notifications WHERE user_id=?"
            " AND COALESCE(read_at,'')<>''", ("u1",)).fetchone()
        self.assertLessEqual(read_rows["n"], notices.KEEP)
        self.assertEqual(notices.unread(self.conn, "u1"), 1)

    def test_an_unknown_kind_becomes_account(self):
        nid = notices.notify(self.conn, "u1", "gossip", "hello")
        row = self.conn.execute("SELECT kind FROM notifications WHERE id=?",
                                (nid,)).fetchone()
        self.assertEqual(row["kind"], "account")


class TestNoticeRoutes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        root = os.path.join(self.tmp, "plugins")
        write_plugin(root, "reader", "fake", order=10, body=READER_BODY)
        write_plugin(root, "speech", "fake", body=SPEECH_BODY)
        write_plugin(root, "storage", "mem", body=STORAGE_BODY)
        self.app = App(root, db_path=os.path.join(self.tmp, "db.sqlite"),
                       storage_id="mem")
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()

    def _req(self, path, obj=None):
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        if obj is None:
            req = urllib.request.Request(url)
        else:
            req = urllib.request.Request(url, data=json.dumps(obj).encode(),
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_needs_somebody_to_tell(self):
        s, _ = self._req("/notifications")
        self.assertEqual(s, 401)
        s, _ = self._req("/notifications/read", {})
        self.assertEqual(s, 401)

    def test_empty_bell_says_nothing(self):
        s, d = self._req("/notifications?user=u1")
        self.assertEqual(s, 200)
        self.assertEqual(d, {"items": [], "unread": 0})

    def test_a_finished_run_leaves_a_row(self):
        s, imp = self._req("/import", {"user": "u1", "kind": "upload",
                                       "filename": "x.epub"})
        self.assertEqual(s, 200)
        bid = imp["book_id"]
        s, r = self._req("/render", {"user": "u1", "book": bid,
                                     "provider": "fake", "voice": "v"})
        self.assertEqual(s, 200)
        end = time.time() + 10
        job = {}
        while time.time() < end:
            s, job = self._req("/job?id=%s" % r["job_id"])
            if job.get("status") in ("done", "error"):
                break
            time.sleep(0.05)
        self.assertEqual(job.get("status"), "done")
        # the worker writes the notice after the job row, so give it a moment
        end = time.time() + 5
        items = []
        while time.time() < end and not items:
            s, d = self._req("/notifications?user=u1")
            items = d.get("items") or []
            if not items:
                time.sleep(0.05)
        self.assertEqual(len(items), 1, items)
        self.assertEqual(items[0]["kind"], "job")
        self.assertIn("ready to play", items[0]["text"])
        self.assertEqual(items[0]["route"], "#/play/" + bid)
        self.assertEqual(d["unread"], 1)

    def test_reading_one_leaves_the_others_unread(self):
        notices.notify(self.app.conn, "u1", "billing", "Plan changed.")
        second = notices.notify(self.app.conn, "u1", "billing", "Renewal stopping.")
        s, d = self._req("/notifications/read", {"user": "u1", "ids": [second]})
        self.assertEqual(s, 200)
        self.assertEqual(d["unread"], 1)
        s, d = self._req("/notifications/read", {"user": "u1"})
        self.assertEqual(d["unread"], 0)
        s, listing = self._req("/notifications?user=u1")
        self.assertEqual(len(listing["items"]), 2)
        self.assertFalse(any(n["unread"] for n in listing["items"]))


if __name__ == "__main__":
    unittest.main()