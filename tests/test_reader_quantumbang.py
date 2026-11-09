"""quantumbang reader: one story split across numbered part pages.

No network. Each case is a small fixture in the shape the site actually emits:
a landing page that links -1/3-, -2/3-, -3/3-, part pages that repeat the same
metadata block at the top, a comment thread under each, and a rel="next" that
runs out of the story. The live site was used to build this; the fixtures are
what keeps it from regressing without a request.
"""

import unittest

from plugins.reader.quantumbang import plugin as qb


HEADER = """<p>Title: Hidden Resources</p>
<p>Author: MeyariMcFarland</p>
<p>Fandom: Harry Potter</p>
<p>Summary: Six years into Hogwarts and Harry was well used to being shuffled off.</p>
"""

COMMENTS = """<div id="comments"><h3>4 Comments</h3><p>Bravo! Please write more.</p>
<p>Leave a Reply</p></div>"""


def part(n, total, body):
    return """<html><head><title>Hidden Resources -%d/%d- MeyariMcFarland &#8211; Quantum Bang</title></head>
<body><div class="entry-content">%s
<p>%s</p></div>%s
<a href="https://quantumbang.org/some-other-series-2-3-someone/" rel="next">Next</a>
</body></html>""" % (n, total, HEADER, body, COMMENTS)


LANDING = """<html><head><title>Hidden Resources &#8211; Quantum Bang</title></head><body>
<div class="entry-content"><p>Read the story:</p>
<a href="https://quantumbang.org/hidden-resources-3-3-meyarimcfarland/">-3/3-</a>
<a href="https://quantumbang.org/hidden-resources-1-3-meyarimcfarland/">-1/3-</a>
<a href="https://quantumbang.org/hidden-resources-2-3-meyarimcfarland/">-2/3-</a>
<a href="https://quantumbang.org/tag/hidden-resources/">tag</a>
<a href="https://quantumbang.org/artist-showcase-ani-for-hidden-resources/">art</a>
</div></body></html>"""

PARTS = {
    "https://quantumbang.org/hidden-resources-1-3-meyarimcfarland/":
        part(1, 3, "Beginning of the story, which is long enough to be read aloud. " * 3),
    "https://quantumbang.org/hidden-resources-2-3-meyarimcfarland/":
        part(2, 3, "Middle of the story, which is long enough to be read aloud. " * 3),
    "https://quantumbang.org/hidden-resources-3-3-meyarimcfarland/":
        part(3, 3, "End of the story, which is long enough to be read aloud. " * 3),
}


class FakeFetch:
    def __init__(self, pages):
        self.pages = pages
        self.asked = []

    def __call__(self, url, cred=None, credential_hosts=()):
        self.asked.append(url)
        if url not in self.pages:
            raise AssertionError("reader asked for a page outside the story: %s" % url)
        return self.pages[url]


class TestSlugFamily(unittest.TestCase):
    def test_part_marker_is_read_from_the_slug(self):
        self.assertEqual(qb.split_slug("hidden-resources-2-3-meyarimcfarland"),
                         ("hidden-resources-meyarimcfarland", 2, 3))

    def test_landing_page_has_no_part(self):
        self.assertEqual(qb.split_slug("hidden-resources-meyarimcfarland"),
                         ("hidden-resources-meyarimcfarland", None, None))

    def test_a_year_or_an_id_is_not_a_part(self):
        self.assertEqual(qb.split_slug("2024-05-post")[1], None)
        self.assertEqual(qb.split_slug("chapter-2-90-notes")[1], None)   # 90 > cap

    def test_only_this_site_matches(self):
        o = qb.Quantumbang()
        self.assertTrue(o.matches({"kind": "url",
                                   "url": "https://quantumbang.org/hidden-resources/"}))
        self.assertTrue(o.matches({"kind": "url",
                                   "url": "https://www.quantumbang.org/x/"}))
        self.assertFalse(o.matches({"kind": "url", "url": "https://notquantumbang.org/x/"}))
        self.assertFalse(o.matches({"kind": "upload"}))


class TestHeader(unittest.TestCase):
    def test_metadata_block_is_peeled_off(self):
        text = ("Title: Hidden Resources\n\nAuthor: Meyari\n\nFandom: HP\n\n"
                "Summary: A story.\n\nThe real first line.")
        fields, body = qb.split_header(text)
        self.assertEqual(fields["Title"], "Hidden Resources")
        self.assertEqual(body, "The real first line.")

    def test_a_paragraph_with_a_colon_is_not_a_header(self):
        text = "He said: no.\n\nShe said: yes.\n\nThey left."
        fields, body = qb.split_header(text)
        self.assertEqual(fields, {})
        self.assertEqual(body, text)


class TestReadStory(unittest.TestCase):
    def test_landing_page_becomes_one_book_of_ordered_parts(self):
        pages = dict(PARTS)
        pages["https://quantumbang.org/hidden-resources-meyarimcfarland/"] = LANDING
        f = FakeFetch(pages)
        book = qb.Quantumbang().read(
            {"kind": "url", "url": "https://quantumbang.org/hidden-resources-meyarimcfarland/"}, f)
        self.assertEqual(book.title, "Hidden Resources")
        self.assertEqual(len(book.chapters), 3)
        self.assertTrue(book.chapters[0].text.startswith("Beginning"))
        self.assertTrue(book.chapters[1].text.startswith("Middle"))
        self.assertTrue(book.chapters[2].text.startswith("End"))
        self.assertEqual([c.title for c in book.chapters],
                         ["Part 1 of 3", "Part 2 of 3", "Part 3 of 3"])
        self.assertEqual(book.meta["author"], "MeyariMcFarland")
        self.assertEqual(book.meta["source"], "quantumbang")
        self.assertEqual(book.meta["parts"], 3)

    def test_entering_on_a_part_still_gets_the_whole_story(self):
        pages = dict(PARTS)
        pages["https://quantumbang.org/hidden-resources-2-3-meyarimcfarland/"] = (
            PARTS["https://quantumbang.org/hidden-resources-2-3-meyarimcfarland/"]
            .replace("</div>", "".join(
                '<a href="%s">p</a>' % u for u in PARTS) + "</div>"))
        f = FakeFetch(pages)
        book = qb.Quantumbang().read(
            {"kind": "url", "url": "https://quantumbang.org/hidden-resources-2-3-meyarimcfarland/"}, f)
        self.assertEqual(len(book.chapters), 3)
        self.assertTrue(book.chapters[0].text.startswith("Beginning"))

    def test_the_repeated_header_is_read_once(self):
        pages = dict(PARTS)
        pages["https://quantumbang.org/hidden-resources-meyarimcfarland/"] = LANDING
        book = qb.Quantumbang().read(
            {"kind": "url", "url": "https://quantumbang.org/hidden-resources-meyarimcfarland/"},
            FakeFetch(pages))
        for ch in book.chapters:
            self.assertNotIn("Author: MeyariMcFarland", ch.text)
            self.assertNotIn("Title: Hidden Resources", ch.text)

    def test_comments_are_not_read_aloud(self):
        pages = dict(PARTS)
        pages["https://quantumbang.org/hidden-resources-meyarimcfarland/"] = LANDING
        book = qb.Quantumbang().read(
            {"kind": "url", "url": "https://quantumbang.org/hidden-resources-meyarimcfarland/"},
            FakeFetch(pages))
        for ch in book.chapters:
            self.assertNotIn("Bravo", ch.text)
            self.assertNotIn("Leave a Reply", ch.text)

    def test_rel_next_out_of_the_story_is_not_followed(self):
        pages = dict(PARTS)
        pages["https://quantumbang.org/hidden-resources-meyarimcfarland/"] = LANDING
        f = FakeFetch(pages)
        qb.Quantumbang().read(
            {"kind": "url", "url": "https://quantumbang.org/hidden-resources-meyarimcfarland/"}, f)
        self.assertFalse(any("some-other-series" in u for u in f.asked))

    def test_a_lone_part_is_one_chapter_not_an_error(self):
        url = "https://quantumbang.org/hidden-resources-2-3-meyarimcfarland/"
        book = qb.Quantumbang().read({"kind": "url", "url": url},
                                     FakeFetch({url: PARTS[url]}))
        self.assertEqual(len(book.chapters), 1)
        self.assertTrue(book.chapters[0].text.startswith("Middle"))

    def test_a_thin_part_page_still_raises(self):
        url = "https://quantumbang.org/hidden-resources-1-3-meyarimcfarland/"
        thin = "<html><body><p>nothing here</p></body></html>"
        with self.assertRaises(ValueError):
            qb.Quantumbang().read({"kind": "url", "url": url}, FakeFetch({url: thin}))


# Cat pasted https://quantumbang.org/the-holmes-factor-by-proffrankly/ and got
# the title page. The landing page's slug is typed by hand and carries a "by"
# the part slugs do not, so the exact family key said the parts belonged to a
# different story and the landing page read as one short chapter.
HAND_TYPED_LANDING = """<html><head><title>The Holmes Factor by ProfFrankly &#8211; Quantum Bang</title></head>
<body><div class="entry-content">%s
<p>The Holmes Factor &#8211; 1/2 &#8211; ProfFrankly</p>
<p><a href="https://quantumbang.org/the-holmes-factor-1-2-proffrankly/">1/2</a></p>
<p><a href="https://quantumbang.org/the-holmes-factor-2-2-proffrankly/">2/2</a></p>
</div></body></html>""" % HEADER


class TestHandTypedLandingSlug(unittest.TestCase):
    def pages(self):
        return {
            "https://quantumbang.org/the-holmes-factor-by-proffrankly/": HAND_TYPED_LANDING,
            "https://quantumbang.org/the-holmes-factor-1-2-proffrankly/":
                part(1, 2, "The story opens on Privet Drive. " * 5),
            "https://quantumbang.org/the-holmes-factor-2-2-proffrankly/":
                part(2, 2, "The story closes on Baker Street. " * 5),
        }

    def test_the_parts_are_found_through_the_extra_word(self):
        found = qb.parts_in_page(HAND_TYPED_LANDING,
                                 "https://quantumbang.org/the-holmes-factor-by-proffrankly/")
        self.assertEqual(sorted(found), [(1, 2), (2, 2)])

    def test_the_landing_page_becomes_the_whole_story(self):
        book = qb.Quantumbang().read(
            {"kind": "url", "url": "https://quantumbang.org/the-holmes-factor-by-proffrankly/"},
            FakeFetch(self.pages()))
        self.assertEqual(len(book.chapters), 2)
        self.assertTrue(book.chapters[0].text.startswith("The story opens"))
        self.assertTrue(book.chapters[1].text.startswith("The story closes"))

    def test_re_importing_through_either_slug_is_the_same_story(self):
        o = qb.Quantumbang()
        self.assertTrue(o.same(
            {"url": "https://quantumbang.org/the-holmes-factor-1-2-proffrankly/"},
            {"url": "https://quantumbang.org/the-holmes-factor-by-proffrankly/"}))
        self.assertFalse(o.same(
            {"url": "https://quantumbang.org/hidden-resources-1-1-other/"},
            {"url": "https://quantumbang.org/the-holmes-factor-by-proffrankly/"}))


if __name__ == "__main__":
    unittest.main()
