"""A word typed on a book page belongs to that book.

Cat typed "DoD" while reading one book and the app offered to re-encode 88
chapters across 35 books. Two causes, both scope: a new word row defaulted to
"All books", and the re-encode check never sent the book it was standing in.
The host has always accepted a `book` filter; the page just never passed one.

This test drives the two functions the page uses, with the network answered by
a stub, so it fails if either default drifts back to the whole library.
"""

import unittest

from tests_ui.support import BrowserTest


class TestWordsScope(BrowserTest):

    def test_a_new_word_defaults_to_the_open_book(self):
        self.register("wordsscope")
        r = self.page.evaluate("""() => {
          book = { id: 'b1', title: 'One Book' };
          return { withBook: defaultScope() };
        }""")
        self.assertEqual(r["withBook"], "book:b1",
                         "a new word would be written against every book")
        # with no book open there is nothing to default to, and that is honest:
        # the empty scope means the reader chose "All books" themselves.
        r = self.page.evaluate("""() => {
          book = null; player = { id: '', title: '', chapters: [] };
          return defaultScope();
        }""")
        self.assertEqual(r, "")

    def test_the_check_asks_about_this_book_and_only_all_books_when_told(self):
        self.register("wordsscope2")
        r = self.page.evaluate("""async () => {
          book = { id: 'b1', title: 'One Book' };
          player = { id: '', title: '', chapters: [] };
          const calls = [];
          window.api = async (u) => { calls.push(u);
            return { chapters_affected: 0, books_affected: 0, books: [] }; };
          await showPronImpact(true);
          await showPronImpact(false, true);
          return calls;
        }""")
        self.assertEqual(r, ["/pron/impact?book=b1", "/pron/impact"],
                         "the default check left the book it was standing in")


if __name__ == "__main__":
    unittest.main()