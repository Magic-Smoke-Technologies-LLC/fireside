"""The switch that starts a render outside the request process.

The worker exists (host/renderjob.py); these tests cover the part that decides
who runs it and the part that decides who is allowed to declare a run dead.
Both were wrong-by-default shapes before: a thread inside the request, and a boot
that assumed no render could possibly be alive.

The local path spawns the real worker as its own process, so the test proves the
seam an operator would use on a box with no container platform at all.
"""

import os
import tempfile
import time
import unittest

from host import jobs, library, renderjob_runner
from host.contracts import Book, Chapter
from host.db import connect

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS = os.path.join(REPO, "plugins")


class RunnerTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="fireside-runner-")
        self._old_data = os.environ.get("FIRESIDE_DATA")
        os.environ["FIRESIDE_DATA"] = self.dir
        os.environ.pop("FIRESIDE_DB_TYPE", None)
        os.environ.pop("FIRESIDE_DB_CONN", None)
        self._old_job = os.environ.pop("FIRESIDE_RENDER_JOB", None)
        self._old_local = os.environ.pop("FIRESIDE_RENDER_LOCAL", None)
        self.conn = connect(None)
        library.create_user(self.conn, "u1")
        storage = self._storage()
        book = Book(title="W", chapters=[Chapter("c0", "aa"), Chapter("c1", "bb")])
        self.bid = library.add_book(self.conn, storage, "u1", book)

    def tearDown(self):
        for k, v in (("FIRESIDE_DATA", self._old_data),
                     ("FIRESIDE_RENDER_JOB", self._old_job),
                     ("FIRESIDE_RENDER_LOCAL", self._old_local)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        try:
            self.conn.close()
        except Exception:
            pass

    def _storage(self):
        from storage.local import LocalBackend
        return LocalBackend(os.path.join(self.dir, "blobs"))

    def _queued(self, **kw):
        return jobs.start_job(self.conn, "u1", self.bid, "local", "tone", total=2,
                              status="queued", **kw)

    def test_start_does_nothing_until_an_operator_asks_for_it(self):
        """Default boxes keep rendering in the request. Moving the work out of
        process is a deployment decision, so start() must say no by itself."""
        jid = self._queued()
        self.assertIsNone(renderjob_runner.start(jid, log_dir=self.dir))
        self.assertEqual(jobs.job(self.conn, jid)["status"], "queued")

    def test_the_local_switch_runs_the_row_in_its_own_process(self):
        os.environ["FIRESIDE_RENDER_LOCAL"] = "1"
        jid = self._queued()
        mode = renderjob_runner.start(jid, log_dir=self.dir)
        self.assertEqual(mode, "local", "the switch did not start a worker")
        deadline = time.time() + 20
        while time.time() < deadline:
            j = jobs.job(self.conn, jid)
            if j["status"] in ("done", "error", "stalled"):
                break
            time.sleep(0.2)
        j = jobs.job(self.conn, jid)
        self.assertEqual(j["status"], "done",
                         "the detached worker did not finish the row: %r" % (j,))
        rows = library.chapters(self.conn, self.bid)
        self.assertTrue(all(r["audio_ref"] for r in rows),
                        "worker finished but left chapters unvoiced")

    def test_a_boot_does_not_bury_a_live_worker(self):
        """The old sweep took every running row at boot. With a worker outside
        the request process that is a live render thrown away."""
        live = self._queued(worker="worker")
        dead = self._queued()   # owned by a request thread, which a boot knows died
        self.conn.execute("UPDATE jobs SET status='running' WHERE id IN (?,?)",
                          (live, dead))
        self.conn.commit()
        jobs.sweep_stale(self.conn, worker="request")
        self.assertEqual(jobs.status_of(self.conn, live), "running",
                         "a boot swept a row a worker still owns")
        self.assertEqual(jobs.status_of(self.conn, dead), "stalled",
                         "a boot left a dead request thread's row running")

    def test_a_user_assigned_identity_is_named_in_the_request(self):
        """The MSI endpoint answers 400 for a bare resource when the identity is
        user-assigned: it has to be told which one. Naming it is the difference
        between a hosted start and a silent fall back (11-12, first live render)."""
        from unittest import mock
        os.environ["IDENTITY_ENDPOINT"] = "http://localhost:9999/msi/token"
        os.environ["IDENTITY_HEADER"] = "hdr"
        os.environ["FIRESIDE_MSI_CLIENT_ID"] = "client-abc"
        seen = {}

        class _R:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def read(self_):
                return b'{"access_token": "tok"}'

        def _open(req, timeout=None):
            seen["url"] = req.full_url
            return _R()

        try:
            with mock.patch("urllib.request.urlopen", _open):
                tok = renderjob_runner._identity_token()
        finally:
            for k in ("IDENTITY_ENDPOINT", "IDENTITY_HEADER",
                      "FIRESIDE_MSI_CLIENT_ID"):
                os.environ.pop(k, None)
        self.assertEqual(tok, "tok")
        self.assertIn("client_id=client-abc", seen["url"])
        self.assertIn("resource=", seen["url"])

    def test_hosted_start_without_an_identity_is_a_quiet_no(self):
        """No identity endpoint means this box is not hosted: it must say no,
        not raise, so the caller can use its own fallback."""
        os.environ.pop("IDENTITY_ENDPOINT", None)
        os.environ.pop("IDENTITY_HEADER", None)
        self.assertIsNone(renderjob_runner._identity_token())
        jid = self._queued()
        self.assertIsNone(renderjob_runner.start_hosted(
            jid, "/subscriptions/x/resourceGroups/y/providers/Microsoft.App/jobs/z"))


if __name__ == "__main__":
    unittest.main()