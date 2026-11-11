"""The model pass on screen: a tick at import, and a card that never hides a guess.

Chris's shape (11-11): a checkbox when importing, the model's answers default to
discard, and a book that already has audio says when its words have moved on.
These drive the page functions with a stubbed network, so they fail if the card
ever starts implying the model changed something on its own.
"""

import unittest

from tests_ui.support import BrowserTest


class TestModelCard(BrowserTest):

    def test_a_suggestion_says_nothing_is_applied_until_you_use_it(self):
        self.register("modelcard")
        r = self.page.evaluate("""async () => {
          book = { id: 'b1', title: 'One Book' };
          MODELSTATE = { available: true, optin: true, status: 'suggested',
                         words: 2, rules: [], note: '', stale: { chapters: 0 } };
          renderBookModel();
          return { text: document.querySelector('#npmodeltext').textContent,
                   hidden: document.querySelector('#npmodel').hidden,
                   buttons: Array.from(document.querySelectorAll('#npmodelbtns button'))
                                  .map(b => b.textContent) };
        }""")
        self.assertFalse(r["hidden"], "a pending suggestion has to be visible")
        self.assertIn("None of them is in your audio until you use them", r["text"])
        self.assertIn("Use these", r["buttons"])
        self.assertIn("Discard", r["buttons"])

    def test_old_audio_asks_rather_than_re_encoding(self):
        self.register("modelcard2")
        r = self.page.evaluate("""async () => {
          book = { id: 'b1', title: 'One Book' };
          let reencoded = 0;
          window.showPronImpact = () => { reencoded++; };
          MODELSTATE = { available: true, optin: false, status: 'discarded',
                         words: 0, rules: [], note: '', stale: { chapters: 3 } };
          renderBookModel();
          document.querySelectorAll('#npmodelbtns button').forEach(
            b => { if (b.textContent.indexOf('Re-encode') === 0) b.click(); });
          return { text: document.querySelector('#npmodeltext').textContent,
                   reencoded };
        }""")
        self.assertIn("3 chapters here were voiced before", r["text"])
        self.assertEqual(r["reencoded"], 1,
                         "the offer has to be a button, not a silent re-encode")

    def test_a_book_the_model_was_never_asked_about_says_so_quietly(self):
        self.register("modelcard3")
        r = self.page.evaluate("""async () => {
          book = { id: 'b1', title: 'One Book' };
          MODELSTATE = { available: false, optin: false, status: '',
                         words: 0, rules: [], note: '', stale: null };
          renderBookModel();
          return document.querySelector('#npmodel').hidden;
        }""")
        self.assertTrue(r, "no box, no check, no card")


if __name__ == "__main__":
    unittest.main()