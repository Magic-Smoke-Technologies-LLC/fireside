"""web reader: a structure page becomes the book behind it.

Cat pasted https://ladyholder.com/the-unlikely-and-unwilling/ and got a title
page. The URL is a real post, but its body is a numbered contents list linking
nine other posts, one chapter each. The web reader read the list as a chapter.
These fixtures are the shape that page actually emits, trimmed: numbered
paragraphs whose anchor is the chapter title, one entry that lists two links,
and a chapter page under each.

The negatives matter as much as the positive: a chapter that links the next
one must stay one chapter, and a messy list must not become a crawl.
"""

import unittest

from plugins.reader.web import plugin as web


def chapter(title, body, extra=""):
    return """<html><head><title>%s &#8211; Ladyholder.com</title></head><body>
<div id="main"><h1 class="entry-title">%s</h1>
<div class="entry-content">
<p>Title: %s</p>
<p>Author: Ladyholder</p>
<p>Warnings: None</p>
<p>%s</p>%s
</div>
<div id="comments"><p>1. Great story, more please.</p>
<a href="https://ladyholder.com/some-other-post/">other</a></div>
</div></body></html>""" % (title, title, title, body, extra)


HUB = """<html><head><title>The Unlikely and Unwilling &#8211; Ladyholder.com</title></head>
<body><div id="main"><h1 class="entry-title">The Unlikely and Unwilling</h1>
<div class="entry-content">
<div class="wp-caption"><p class="wp-caption-text">Being defined by their genes wasn&#8217;t
what they wanted.</p></div>
<p><strong>1. <a href="https://ladyholder.com/2010/08/15/an-unlikely-guide/">The Unlikely Guide</a></strong></p>
<p>There was just no way that he was a Guide. Really.</p>
<p><strong>2. <a href="https://ladyholder.com/2010/08/15/the-unwilling-sentinel/">The Unwilling Sentinel</a></strong></p>
<p>John wasn&#8217;t willing to give up the sky.</p>
<p><strong>3. <a href="https://ladyholder.com/2010/08/15/the-unexpected-pair/">The Unexpected Pair</a></strong></p>
<p>Doing things the easy way, just wasn&#8217;t them.</p>
<p>4. <strong>The Unconquered Pair</strong>
<a href="https://ladyholder.com/2025/03/the-unconquered-pair-part-i/">Part I</a> |
<a href="https://ladyholder.com/2025/03/the-unconquered-pair-part-ii/">Part II</a></p>
</div>
<div id="comments"><h3>14 Comments</h3><p>3. I loved this one!</p>
<a href="https://ladyholder.com/2010/08/15/comment-link/">reply</a></div>
</div></body></html>"""

CHAPTERS = {
    "https://ladyholder.com/2010/08/15/an-unlikely-guide/":
        chapter("An Unlikely Guide", "Rodney knew he was not a Guide. " * 8),
    "https://ladyholder.com/2010/08/15/the-unwilling-sentinel/":
        chapter("The Unwilling Sentinel", "John was not willing to give up the sky. " * 8),
    "https://ladyholder.com/2010/08/15/the-unexpected-pair/":
        chapter("The Unexpected Pair", "Doing things the easy way was not them. " * 8),
    "https://ladyholder.com/2025/03/the-unconquered-pair-part-i/":
        chapter("The Unconquered Pair Part I", "The expedition knuckled down. " * 8),
    "https://ladyholder.com/2025/03/the-unconquered-pair-part-ii/":
        chapter("The Unconquered Pair Part 2", "The other shoe dropped. " * 8),
}

HUB_URL = "https://ladyholder.com/the-unlikely-and-unwilling/"


class FakeFetch:
    def __init__(self, pages):
        self.pages = pages
        self.asked = []

    def __call__(self, url, cred=None, credential_hosts=()):
        self.asked.append(url)
        if url not in self.pages:
            raise AssertionError("reader asked for a page off the contents list: %s" % url)
        return self.pages[url]


def read(pages, url=HUB_URL):
    return web.Web().read({"kind": "url", "url": url}, FakeFetch(pages))


class TestContentsPage(unittest.TestCase):
    def test_a_numbered_list_becomes_one_book_in_order(self):
        pages = dict(CHAPTERS)
        pages[HUB_URL] = HUB
        book = read(pages)
        self.assertEqual(book.title, "The Unlikely and Unwilling")
        self.assertEqual([c.title for c in book.chapters],
                         ["An Unlikely Guide", "The Unwilling Sentinel",
                          "The Unexpected Pair", "The Unconquered Pair Part I",
                          "The Unconquered Pair Part 2"])
        self.assertTrue(book.chapters[0].text.startswith("Rodney knew"))
        self.assertEqual(book.meta["author"], "Ladyholder")
        self.assertEqual(book.meta["source"], "web")
        self.assertEqual(book.meta["index"], 5)

    def test_the_metadata_block_is_not_read_aloud(self):
        pages = dict(CHAPTERS)
        pages[HUB_URL] = HUB
        body = read(pages).chapters[0].text
        self.assertNotIn("Author: Ladyholder", body)
        self.assertNotIn("Title: An Unlikely Guide", body)

    def test_the_hub_is_not_its_own_chapter(self):
        pages = dict(CHAPTERS)
        pages[HUB_URL] = HUB
        f = FakeFetch(pages)
        web.Web().read({"kind": "url", "url": HUB_URL}, f)
        self.assertEqual(f.asked.count(HUB_URL), 1)

    def test_a_single_link_entry_is_two_chapters(self):
        pages = dict(CHAPTERS)
        pages[HUB_URL] = HUB
        self.assertEqual(len(read(pages).chapters), 5)


class TestNotAContentsPage(unittest.TestCase):
    def test_a_chapter_that_links_the_next_chapter_stays_one_chapter(self):
        page = chapter("An Unlikely Guide", "Rodney knew he was not a Guide. " * 20,
                       '<p><a href="https://ladyholder.com/2010/08/15/next/">Next</a></p>')
        entries = web.index_entries(page, "https://ladyholder.com/2010/08/15/an-unlikely-guide/")
        self.assertEqual(entries, [])
        book = read({"https://ladyholder.com/2010/08/15/an-unlikely-guide/": page},
                    url="https://ladyholder.com/2010/08/15/an-unlikely-guide/")
        self.assertEqual(len(book.chapters), 1)

    def test_numbers_out_of_order_are_not_a_list(self):
        page = HUB.replace("<strong>1. ", "<strong>2. ", 1)
        self.assertEqual(web.index_entries(page, HUB_URL), [])

    def test_labels_count_too(self):
        page = """<html><body><div class="entry-content">
        <p>Contents.</p>
        <p><a href="/one">Chapter One</a></p>
        <p><a href="/two">Chapter Two</a></p>
        <p><a href="/three">Chapter Three</a></p>
        </div></body></html>"""
        entries = web.index_entries(page, "https://example.com/book/")
        self.assertEqual([e["n"] for e in entries], [1, 2, 3])

    def test_two_entries_is_not_a_list(self):
        page = HUB
        while page.count("<strong>") > 2:
            i = page.rindex("<p><strong>")
            page = page[:i] + "</div></div></body></html>"
        self.assertEqual(web.index_entries(page, HUB_URL), [])

    def test_a_post_linking_other_posts_numbers_is_still_a_post(self):
        page = """<html><body><div class="entry-content">
        <p>%s</p>
        <p>1. <a href="https://example.com/a">A</a></p>
        <p>2. <a href="https://example.com/b">B</a></p>
        <p>3. <a href="https://example.com/c">C</a></p>
        </div></body></html>""" % ("My week in review. " * 300)
        self.assertEqual(web.index_entries(page, "https://example.com/post/"), [])

    def test_an_entry_pointing_off_site_is_not_followed(self):
        page = HUB.replace("https://ladyholder.com/2010/08/15/the-unexpected-pair/",
                           "https://elsewhere.example/2010/08/15/the-unexpected-pair/")
        self.assertEqual(len(web.index_entries(page, HUB_URL)), 0)

    def test_a_dead_entry_is_one_missing_chapter_not_no_book(self):
        pages = dict(CHAPTERS)
        pages[HUB_URL] = HUB
        del pages["https://ladyholder.com/2010/08/15/the-unwilling-sentinel/"]
        book = read(pages)
        self.assertEqual(len(book.chapters), 4)
        self.assertEqual(book.chapters[1].title, "The Unexpected Pair")


if __name__ == "__main__":
    unittest.main()
