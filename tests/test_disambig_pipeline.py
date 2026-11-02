"""The pre-render model pass: off by default, one book's readings decided once.

The interesting behaviours are the gates, not the model: an account that is not
named must cost nothing, a second render must not ask again, and a box that is
asleep must leave the render alone.
"""

import os
import tempfile
import unittest

from host import disambig, jobs, library, pron
from host.contracts import Audio, Book, Chapter, Quote, Report
from host.db import connect
from storage.local import LocalBackend

jobs.SYNTH_BACKOFF = 0.0

UID = "u_cat"
BID_KEY = "book-1"


class FakeSpeech:
    id = "fake"
    ext = "mp3"
    mime = "audio/mpeg"
    needs_key = False

    def synth(self, text, voice, opts, cred):
        return Audio(data=b"audio", ext="mp3", mime="audio/mpeg")

    def list_voices(self, cred):
        return []

    def quote(self, ctx):
        return Quote()

    def report(self, ctx):
        return Report(charge="charged")


class Env:
    """Set the three FIRESIDE_DISAMBIG_* variables for the length of a test."""

    def __init__(self, accounts=UID, url="http://box:11434/v1", model="qwen2.5:3b"):
        self.vals = {"FIRESIDE_DISAMBIG_ACCOUNTS": accounts,
                     "FIRESIDE_DISAMBIG_URL": url,
                     "FIRESIDE_DISAMBIG_MODEL": model}

    def __enter__(self):
        self.old = {k: os.environ.get(k) for k in self.vals}
        for k, v in self.vals.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return self

    def __exit__(self, *a):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fireside-disambig-")
        self.storage = LocalBackend(self.tmp + "/blobs")
        self.conn = connect(":memory:")
        library.create_user(self.conn, UID)
        self.bid = library.add_book(self.conn, self.storage, UID,
                                    Book(title="B", chapters=[
                                        Chapter("one", "We live here, and the signal is live."),
                                        Chapter("two", "A tear in the page.")]))

    def run_pass(self, **kw):
        idxs = [r["idx"] for r in library.chapters(self.conn, self.bid)]
        kw.setdefault("run", lambda text, model, cred: [
            {"word": "live", "say": "liv", "why": "we live here"}])
        return disambig.ensure(self.conn, self.storage, UID, self.bid, idxs, **kw)


class TestGates(Base):
    def test_nobody_is_enabled_without_being_named(self):
        with Env(accounts=""):
            self.assertFalse(disambig.enabled_for(UID))
        with Env(accounts="someone-else"):
            self.assertFalse(disambig.enabled_for(UID))
        with Env():
            self.assertTrue(disambig.enabled_for(UID))

    def test_an_unconfigured_box_runs_for_nobody(self):
        with Env(url=""):
            self.assertFalse(disambig.enabled_for(UID))
        with Env(model=""):
            self.assertFalse(disambig.enabled_for(UID))

    def test_a_disabled_account_never_reaches_the_model(self):
        called = []
        with Env(accounts="someone-else"):
            rules, err = self.run_pass(run=lambda *a: called.append(1) or [])
        self.assertEqual((rules, err), ([], ""))
        self.assertEqual(called, [])
        self.assertIsNone(disambig.existing(self.conn, UID, self.bid))


class TestThePass(Base):
    def test_answers_are_stored_as_this_books_rules(self):
        with Env():
            rules, err = self.run_pass()
        self.assertEqual(err, "")
        self.assertEqual([r["from"] for r in rules], ["live"])
        d = disambig.existing(self.conn, UID, self.bid)
        self.assertIsNotNone(d)
        self.assertEqual(d["scope"], "book")
        self.assertEqual(d["scope_key"], self.bid)
        self.assertEqual(d["rules"][0]["to"], "liv")

    def test_the_saved_rules_reach_the_render(self):
        with Env():
            self.run_pass()
        rules = pron.for_book(pron.load(self.conn, UID), self.bid)
        self.assertIn("liv", [r["to"] for r in rules])
        self.assertEqual(pron.apply("We live here.", rules), "We liv here.")

    def test_one_book_is_asked_once(self):
        calls = []

        def run(text, model, cred):
            calls.append(text)
            return [{"word": "live", "say": "liv"}]

        with Env():
            self.run_pass(run=run)
            rules, err = self.run_pass(run=run)
        self.assertEqual(len(calls), 1)
        self.assertEqual((rules, err), ([], ""))
        self.assertEqual(pron.for_book(pron.load(self.conn, UID), self.bid)[0]["to"], "liv")

    def test_a_sleeping_box_does_not_fail_the_render(self):
        def boom(*a):
            raise RuntimeError("the model server did not answer")

        with Env():
            rules, err = self.run_pass(run=boom)
        self.assertEqual(rules, [])
        self.assertIn("did not answer", err)
        self.assertIsNone(disambig.existing(self.conn, UID, self.bid))

    def test_a_failed_pass_is_not_remembered_as_done(self):
        with Env():
            self.run_pass(run=lambda *a: (_ for _ in ()).throw(RuntimeError("down")))
            rules, err = self.run_pass()
        self.assertEqual(err, "")
        self.assertEqual([r["from"] for r in rules], ["live"])


class TestWhatIsRefused(unittest.TestCase):
    def test_a_respelling_must_be_a_word_not_a_sentence(self):
        out = disambig._rules(BID_KEY, [
            {"word": "live", "say": "liv"},
            {"word": "live", "say": "lyve"},           # first answer wins
            {"word": "read", "say": "read"},           # not a fix
            {"word": "the whole phrase", "say": "x"},  # not a word
            {"word": "wound", "say": ""},
            {"word": "bow", "say": "a very long explanation that is prose, not a spelling"},
        ])
        self.assertEqual([(r["from"], r["to"]) for r in out], [("live", "liv")])

    def test_rules_land_on_the_book_and_nowhere_else(self):
        out = disambig._rules(BID_KEY, [{"word": "live", "say": "liv"}])
        self.assertEqual(out[0]["book"], BID_KEY)
        self.assertEqual(out[0]["author"], "")
        self.assertEqual(out[0]["series"], "")


class TestRenderHook(Base):
    def test_an_enabled_account_renders_through_the_rules(self):
        speech = FakeSpeech()
        with Env():
            from host import disambig as d
            old = d._plugin
            d._plugin = lambda root=None: type("P", (), {
                "decide": staticmethod(lambda text, model, cred: [
                    {"word": "live", "say": "liv"}])})
            try:
                jid = jobs.render_book(self.conn, self.storage, speech, UID, self.bid,
                                       voice="v1", model="m1")
            finally:
                d._plugin = old
        self.assertEqual(jobs.job(self.conn, jid)["status"], "done")
        rules = pron.for_book(pron.load(self.conn, UID), self.bid)
        self.assertEqual(pron.spoken("We live here.", rules), "We liv here.")
