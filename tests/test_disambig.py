"""The disambiguator: what it asks, what it will not price, and how it fails.

No network. The plugin's HTTP call is replaced, so these check the shape the
host and the UI depend on, not OpenAI's uptime.
"""

import json
import os
import sys
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from plugins.disambig.openai import plugin as P


class TestCandidates(unittest.TestCase):
    def test_only_sentences_holding_a_two_reading_word_are_paid_for(self):
        text = ("The lamp went out. She had to live with it. "
                "A quiet room with nothing in it.")
        got = P._candidates(text)
        self.assertEqual(len(got), 1)
        self.assertIn("live", got[0])

    def test_a_clean_text_costs_nothing_and_asks_nothing(self):
        self.assertEqual(P._candidates("Nothing here can be mispronounced."), [])

    def test_a_repeated_word_is_counted_once_per_sentence(self):
        got = P._candidates("Read it. Read it again.")
        self.assertEqual(len(got), 2)


class TestQuote(unittest.TestCase):
    def setUp(self):
        self.d = P.Disambig()

    def test_a_priced_model_returns_a_number_before_the_run(self):
        q = self.d.quote({"text": "She had to live with it.", "model": "gpt-4o-mini"})
        self.assertTrue(q.authoritative)
        self.assertEqual(q.chars_est, len("She had to live with it."))
        self.assertGreater(q.price, 0)

    def test_an_unpriced_model_says_unknown_and_never_free(self):
        q = self.d.quote({"text": "She had to live with it.", "model": "mystery-7b"})
        self.assertFalse(q.authoritative)
        self.assertIn("mystery-7b", q.refusal_reason)

    def test_text_with_no_candidates_is_a_free_pass(self):
        q = self.d.quote({"text": "Nothing here.", "model": "gpt-4o-mini"})
        self.assertEqual(q.chars_est, 0)
        self.assertEqual(q.price, 0.0)


class TestScan(unittest.TestCase):
    def setUp(self):
        self.d = P.Disambig()
        self.sent = []

    def _answer(self, payload):
        self.sent.append(payload)
        return {"choices": [{"message": {"content":
                '{"questions":[{"word":"live","question":"live to serve or a live wire?",'
                '"options":["liv","lyve"]}]}'}}]}

    def test_the_pass_returns_questions_the_host_can_render(self):
        P._post = lambda url, key, payload: self._answer(payload)
        qs = self.d.scan("She had to live with it.", "gpt-4o-mini", {})
        self.assertEqual(len(qs), 1)
        self.assertEqual(qs[0]["word"], "live")
        self.assertEqual(len(qs[0]["options"]), 2)

    def test_only_the_candidate_sentences_are_sent(self):
        P._post = lambda url, key, payload: self._answer(payload)
        self.d.scan("The lamp went out. She had to live with it.", "m", {})
        sent = self.sent[0]["messages"][1]["content"]
        self.assertIn("live", sent)
        self.assertNotIn("lamp", sent)

    def test_a_broken_answer_shape_is_loud_not_empty(self):
        P._post = lambda url, key, payload: {"choices": []}
        with self.assertRaises(RuntimeError):
            self.d.scan("She had to live with it.", "m", {})

    def test_no_model_is_an_error(self):
        with self.assertRaises(RuntimeError):
            self.d.scan("She had to live with it.", "", {})


class TestDecideKeepsItsWord(unittest.TestCase):
    """The model answers; the plugin decides what counts as a fix.

    A live gpt-4.1-mini answer on one book contained "guide" -> "guyd" and
    "living" -> "liv-ing": a spelling of a word with one reading, and a word
    split into syllables. Both reached a reader's word list as if she had typed
    them. The sentence filter already knows which words are two-reading words;
    a fix about any other word is the model freelancing.
    """

    def setUp(self):
        self.d = P.Disambig()
        self.orig = P._post

    def tearDown(self):
        P._post = self.orig

    def _answer(self, fixes):
        P._post = lambda url, key, payload: {"choices": [{"message": {"content":
            json.dumps({"fixes": fixes})}}]}

    def test_nonsense_is_dropped_and_a_real_fix_survives(self):
        self._answer([
            {"word": "guide", "say": "guyd"},          # one reading, not a fix
            {"word": "living", "say": "liv-ing"},      # syllables, not a sound
            {"word": "winding", "say": "wynding"},     # a real two-reading word
            {"word": "live", "say": "liv"},            # a real two-reading word
        ])
        out = self.d.decide("She had to live with it, winding the clock.",
                            "gpt-4.1-mini", {})
        self.assertEqual([(f["word"], f["say"]) for f in out],
                         [("winding", "wynding"), ("live", "liv")])

    def test_an_inflected_form_of_a_listed_word_counts(self):
        # The sentence is selected by its base word ("live"); the answer about
        # the inflected one ("records" is "record") is still a real fix.
        self._answer([{"word": "records", "say": "rek-ords"}])
        out = self.d.decide("He records the show live.", "m", {})
        self.assertEqual([(f["word"], f["say"]) for f in out],
                         [("records", "rek-ords")])


class TestSettings(unittest.TestCase):
    def test_the_self_hoster_points_it_at_their_own_box(self):
        fields = {f.name: f for f in P.Disambig().settings()}
        self.assertIn("base_url", fields)
        self.assertEqual(fields["key"].kind, "password")

    def test_it_declares_no_model_field(self):
        """The model is chosen per render, not stored on the server."""
        names = [f.name for f in P.Disambig().settings()]
        self.assertNotIn("model", names)


if __name__ == "__main__":
    unittest.main()
