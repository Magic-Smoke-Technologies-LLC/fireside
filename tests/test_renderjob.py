"""The render worker: a queued row rendered by a process that holds no request.

This is what makes a render durable. On the hosted box the work used to be a
thread inside the web process, and the platform is free to stop that process:
`KEDAScaleTargetDeactivated 1->0` on 11-11 killed three of Cat's four queued
books, and the boot after it marked them "stalled".

The tests drive the real entrypoint (`renderjob.run`) against a throwaway data
dir and the real local speech plugin, so the thing under test is the seam a
Container Apps Job will pull: row in, audio and status out, no request context.
"""

import os
import tempfile
import unittest

from host import accounting, jobs, library, renderjob
from host.contracts import Audio, Book, Chapter, Quote, Report
from host.db import connect

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS = os.path.join(REPO, "plugins")


class _Quiet:
    """Silences the worker's own report to stdout during tests."""
    def __call__(self, *a, **k):
        pass


class MeteredSpeech:
    id = "metered"
    ext = "mp3"
    mime = "audio/mpeg"
    needs_key = False

    def synth(self, text, voice, opts, cred):
        return Audio(data=b"tone", ext="mp3", mime="audio/mpeg")

    def list_voices(self, cred):
        return []

    def quote(self, ctx):
        return Quote(price=0.0, charge="metered")

    def report(self, ctx):
        return Report(charge="charged", cost=float(ctx.get("chars_actual") or 0) / 1000.0)


class RenderJobTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="fireside-renderjob-")
        self._old_data = os.environ.get("FIRESIDE_DATA")
        os.environ["FIRESIDE_DATA"] = self.dir
        os.environ.pop("FIRESIDE_DB_TYPE", None)
        os.environ.pop("FIRESIDE_DB_CONN", None)
        self.conn = connect(None)
        library.create_user(self.conn, "u1")
        book = Book(title="W", chapters=[Chapter("c0", "aa"), Chapter("c1", "bb")])
        self.bid = library.add_book(self.conn, None, "u1", book) if False else None
        self.bid = library.add_book(self.conn, self._storage(), "u1", book)

    def tearDown(self):
        if self._old_data is None:
            os.environ.pop("FIRESIDE_DATA", None)
        else:
            os.environ["FIRESIDE_DATA"] = self._old_data
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

    def test_a_queued_row_is_finished_by_the_worker(self):
        jid = self._queued()
        code = renderjob.run(jid, plugins_root=PLUGINS, echo=_Quiet())
        self.assertEqual(code, 0)
        j = jobs.job(self.conn, jid)
        self.assertEqual(j["status"], "done", "the worker did not finish the row")
        rows = library.chapters(self.conn, self.bid)
        self.assertTrue(all(r["audio_ref"] for r in rows),
                        "the worker left chapters unvoiced: %r" % [r["audio_ref"] for r in rows])
        storage = self._storage()
        for r in rows:
            self.assertTrue(storage.has(r["audio_ref"]),
                            "the row points at audio this storage does not have")

    def test_a_row_that_names_chapters_leaves_the_rest_unvoiced(self):
        """A selection is part of the work order. The hosted worker has no
        request to ask, so it must read the chapters off the row; before this,
        "voice chapter 1" voiced the whole book and charged for one chapter."""
        jid = self._queued(only=[1])
        code = renderjob.run(jid, plugins_root=PLUGINS, echo=_Quiet())
        self.assertEqual(code, 0)
        rows = {r["idx"]: r for r in library.chapters(self.conn, self.bid)}
        self.assertTrue(rows[1]["audio_ref"], "the named chapter was not voiced")
        self.assertFalse(rows[0]["audio_ref"],
                         "the worker voiced a chapter the row did not ask for")

    def test_a_cancelled_row_is_not_rendered(self):
        jid = self._queued()
        self.conn.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (jid,))
        self.conn.commit()
        code = renderjob.run(jid, plugins_root=PLUGINS, echo=_Quiet())
        self.assertEqual(code, 0)
        rows = library.chapters(self.conn, self.bid)
        self.assertTrue(all(not r["audio_ref"] for r in rows),
                        "a cancelled job was rendered anyway")

    def test_a_cancel_that_lands_mid_run_stops_the_next_chapter(self):
        """The worker reads the row between chapters, since its cancel flag is
        not a threading.Event it shares with the request."""
        jid = self._queued()
        calls = {"n": 0}
        real = jobs.render_chapter

        def per_chapter(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                # the reader pressed Cancel while chapter one was being voiced
                self.conn.execute("UPDATE jobs SET status='cancelled' WHERE id=?",
                                  (jid,))
                self.conn.commit()
            return real(*a, **kw)

        jobs.render_chapter = per_chapter
        try:
            renderjob.run(jid, plugins_root=PLUGINS, echo=_Quiet())
        finally:
            jobs.render_chapter = real
        j = jobs.job(self.conn, jid)
        self.assertEqual(j["status"], "cancelled")
        rows = library.chapters(self.conn, self.bid)
        self.assertFalse(rows[1]["audio_ref"],
                         "the chapter after the cancel was still voiced")

    def test_the_reservation_settles_in_the_worker_and_never_twice(self):
        accounting.topup(self.conn, "u1", 10.0)
        res = accounting.reserve(self.conn, "u1", 1.0, job_id="j1")
        jid = self._queued(connection_id="", speed=1.0)
        jobs.set_reservation(self.conn, jid, res, quoted_chars=4)
        # The run has already voiced both chapters, so nothing is still stale:
        # the charge is for the four characters the request quoted.
        renderjob.run(jid, plugins_root=PLUGINS, echo=_Quiet())
        before = accounting.balance(self.conn, "u1")
        # The speech object is the one that did the voicing (id 'local'), so
        # "what is still stale" is measured against the right voice; it reports
        # a metered charge so the debit is visible.
        metered = MeteredSpeech()
        metered.id = "local"
        accounting.settle_run(self.conn, self._storage(), metered, self.bid,
                              [0, 1], [], "tone", "", 1.0, res, 4, {}, jid)
        first = accounting.balance(self.conn, "u1")
        self.assertLess(first, before, "the worker did not charge the run")
        accounting.settle_run(self.conn, self._storage(), metered, self.bid,
                              [0, 1], [], "tone", "", 1.0, res, 4, {}, jid)
        self.assertEqual(accounting.balance(self.conn, "u1"), first,
                         "settling the same reservation twice charged twice")


    # The hosted job's container runs --drain: it names no row, because naming
    # one would make the starter carry the row's identity as an argument.

    def _another_book(self, title):
        b = Book(title=title, chapters=[Chapter("c0", "cc")])
        return library.add_book(self.conn, self._storage(), "u1", b)

    def test_drain_finishes_every_queued_row_then_stops(self):
        a = self._queued()
        b = jobs.start_job(self.conn, "u1", self._another_book("V"), "local",
                           "tone", total=1, status="queued")
        renderjob.drain(plugins_root=PLUGINS, echo=_Quiet())
        self.assertEqual(jobs.status_of(self.conn, a), "done")
        self.assertEqual(jobs.status_of(self.conn, b), "done")

    def test_a_claim_cannot_be_taken_twice(self):
        # Two workers may read the same row; only the UPDATE may decide who got it.
        jid = self._queued()
        first = jobs.claim_queued(self.conn)
        self.assertEqual(first, jid)
        self.assertIsNone(jobs.claim_queued(self.conn),
                          "a claimed row was handed to a second worker")


if __name__ == "__main__":
    unittest.main()