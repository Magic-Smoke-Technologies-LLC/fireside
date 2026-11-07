"""keiramarcos reader: one work, published as bundle pages that hold chapters.

No network. The fixtures are the shape the live site emits: a landing PAGE whose
"Story Posts" tab links out to dated posts, each post opening with the same
metadata block, chapter titles as <h1> inside .entry-content, a "Chapters 3-4"
link at the end of each bundle, the sharer and Jetpack like widget after "The
End", and a comment thread underneath. The fixtures are what keeps the live
parse from regressing without a request.
"""

import unittest

from plugins.reader.keiramarcos import plugin as km


HEADER = """<p class="wpwc-reading-time"><b>Reading Time:</b> 115 Minutes</p>
<p><strong>Title</strong>: A Glimpse of Us<br />
<strong>Author</strong>: Keira Marcos<br />
<strong>Fandom</strong>: 9-1-1<br />
<strong>Warnings</strong>: Explicit Sex<br />
<strong>Word Count</strong>: 56,004<br />
<strong>Summary</strong>: A soulmate AU.</p>
<p>* * * *</p>
"""

SHARER = """<div class='sharedaddy sd-block sd-like jetpack-likes-widget-wrapper'>
<h3 class="sd-title">Like this:</h3><p>Like Loading...</p></div>"""

COMMENTS = """<div id="comments"><h3>2 Comments</h3><p>Bravo! Please write more.</p></div>"""


def post(title, chapters, next_url="", total=2):
    """One bundle page: n chapters, each an <h1> with a paragraph under it."""
    body = "".join("<h1>%s</h1><p>%s</p>" % (t, ("%s body text, long enough to be read aloud. " % t) * 3)
                   for t in chapters)
    nxt = ('<h1><a href="%s">Chapters 3-4</a></h1>' % next_url) if next_url else ""
    return """<html><head><title>%s &#8211; Keira Marcos</title></head><body>
<article><h1 class="entry-title">%s</h1>
<div class="entry-content">%s%s%s</div>
%s
</article></body></html>""" % (title, title, HEADER, body, nxt, COMMENTS + SHARER)


LANDING = """<html><head><title>A Glimpse of Us &#8211; Keira Marcos</title></head><body>
<h1 class="entry-title">A Glimpse of Us</h1>
<div class="entry-content"><p>Story summary here.</p>
<div class="responsive-tabs">
<h2 class="tabtitle">Story Posts</h2>
<div class="tabcontent">
<h1><a href="https://keiramarcos.com/2024/03/a-glimpse-of-us-1-2/">Chapters 1-2</a> |
<a href="https://keiramarcos.com/2024/03/a-glimpse-of-us-2-2/">Chapters 3-4</a></h1>
</div>
<h2 class="tabtitle">Casting</h2>
<div class="tabcontent">
<a href="https://keiramarcos.com/2024/03/a-glimpse-of-us-casting/">the cast</a>
</div>
</div></div><div class='sharedaddy sd-like'><p>Like Loading...</p></div>
</body></html>"""

P1 = "https://keiramarcos.com/2024/03/a-glimpse-of-us-1-2/"
P2 = "https://keiramarcos.com/2024/03/a-glimpse-of-us-2-2/"
WORK = "https://keiramarcos.com/fan-fiction/a-glimpse-of-us/"

PAGES = {
    WORK: LANDING,
    P1: post("A Glimpse of Us &#8211; 1/2", ["Chapter 1", "Chapter 2"], next_url=P2),
    P2: post("A Glimpse of Us &#8211; 2/2", ["Chapter 3", "Chapter 4"]),
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


class TestAddressing(unittest.TestCase):
    def test_part_marker_is_read_from_the_slug(self):
        self.assertEqual(km.split_part("a-glimpse-of-us-2-2"), ("a-glimpse-of-us", 2, 2))

    def test_landing_page_has_no_part(self):
        self.assertEqual(km.split_part("a-glimpse-of-us"), ("a-glimpse-of-us", None, None))

    def test_a_year_or_an_id_is_not_a_part(self):
        self.assertIsNone(km.split_part("2024-03")[1])
        self.assertIsNone(km.split_part("chapter-2-90")[1])      # 90 > cap

    def test_every_url_of_the_work_names_one_book(self):
        self.assertEqual(km.identity_of(WORK), km.identity_of(P1))
        self.assertEqual(km.identity_of(P2), km.identity_of(WORK))
        self.assertNotEqual(km.identity_of(WORK),
                            km.identity_of("https://keiramarcos.com/fan-fiction/daylight/"))

    def test_a_pasted_part_page_guesses_the_work_page(self):
        self.assertEqual(km.landing_of(P2), WORK)

    def test_only_this_site_matches(self):
        k = km.Keiramarcos()
        self.assertTrue(k.matches({"kind": "url", "url": WORK}))
        self.assertTrue(k.matches({"kind": "url", "url": "https://www.keiramarcos.com/x/"}))
        self.assertFalse(k.matches({"kind": "url", "url": "https://notkeiramarcos.com/x/"}))
        self.assertFalse(k.matches({"kind": "upload"}))


class TestParse(unittest.TestCase):
    def test_chapters_come_from_headings_not_from_pages(self):
        book = km.Keiramarcos().read({"kind": "url", "url": WORK}, FakeFetch(dict(PAGES)))
        self.assertEqual(book.title, "A Glimpse of Us")
        self.assertEqual([c.title for c in book.chapters],
                         ["Chapter 1", "Chapter 2", "Chapter 3", "Chapter 4"])
        self.assertTrue(book.chapters[2].text.startswith("Chapter 3 body"))
        self.assertEqual(book.meta["author"], "Keira Marcos")
        self.assertEqual(book.meta["word_count"], "56,004")
        self.assertEqual(book.meta["parts"], 2)
        self.assertEqual(book.meta["source"], "keiramarcos")

    def test_the_metadata_block_is_read_once_and_dropped(self):
        book = km.Keiramarcos().read({"kind": "url", "url": WORK}, FakeFetch(dict(PAGES)))
        for ch in book.chapters:
            self.assertNotIn("Author: Keira Marcos", ch.text)
            self.assertNotIn("Reading Time", ch.text)
            self.assertNotIn("Title: A Glimpse of Us", ch.text)

    def test_the_next_bundle_link_is_navigation_not_a_chapter(self):
        book = km.Keiramarcos().read({"kind": "url", "url": WORK}, FakeFetch(dict(PAGES)))
        self.assertNotIn("Chapters 3-4", [c.title for c in book.chapters])
        self.assertTrue(all("Chapters 3-4" not in c.text for c in book.chapters))

    def test_the_sharer_and_like_widget_are_not_read_aloud(self):
        book = km.Keiramarcos().read({"kind": "url", "url": WORK}, FakeFetch(dict(PAGES)))
        for ch in book.chapters:
            self.assertNotIn("Like Loading", ch.text)
            self.assertNotIn("Share this", ch.text)
            self.assertNotIn("Bravo", ch.text)

    def test_a_tab_that_is_not_the_story_is_not_crawled(self):
        f = FakeFetch(dict(PAGES))
        km.Keiramarcos().read({"kind": "url", "url": WORK}, f)
        self.assertFalse(any("casting" in u for u in f.asked))

    def test_a_standalone_post_is_one_bundle_of_chapters(self):
        # no work page reachable: the bundle page is the whole story we can see
        book = km.Keiramarcos().read({"kind": "url", "url": P2},
                                     FakeFetch({P2: PAGES[P2]}))
        self.assertEqual([c.title for c in book.chapters], ["Chapter 3", "Chapter 4"])
        self.assertEqual(book.title, "A Glimpse of Us")

    def test_pasting_the_second_bundle_still_gets_the_whole_work(self):
        f = FakeFetch(dict(PAGES))
        book = km.Keiramarcos().read({"kind": "url", "url": P2}, f)
        self.assertEqual([c.title for c in book.chapters],
                         ["Chapter 1", "Chapter 2", "Chapter 3", "Chapter 4"])
        self.assertEqual(book.meta["url"], WORK)

    def test_a_thin_page_raises(self):
        url = "https://keiramarcos.com/2024/03/empty-1-1/"
        thin = "<html><body><p>nothing here</p></body></html>"
        with self.assertRaises(ValueError):
            km.Keiramarcos().read({"kind": "url", "url": url},
                                 FakeFetch({url: thin, km.landing_of(url): thin}))

    def test_a_series_page_is_refused_by_name_not_folded_into_one_book(self):
        from host.contracts import Refused
        series = """<html><body><h1 class="entry-title">Hold My Coffee (Series)</h1>
<div class="entry-content"><div class="responsive-tabs">
<h2 class="tabtitle">Stories</h2><div class="tabcontent">
<h1><a href="https://keiramarcos.com/2017/02/a-caged-tiger/">1. A Caged Tiger</a></h1>
<h1><a href="https://keiramarcos.com/2017/02/an-empty-coffin/">2. An Empty Coffin</a></h1>
</div><h2 class="tabtitle">Cast</h2><div class="tabcontent">
<a href="https://keiramarcos.com/fan-fiction/x/holdmycoffee/johnsheppard/">John</a>
</div></div></div></body></html>"""
        url = "https://keiramarcos.com/fan-fiction/stargate-atlantis-fan-fiction/holdmycoffee/"
        with self.assertRaises(Refused) as ctx:
            km.Keiramarcos().read({"kind": "url", "url": url}, FakeFetch({url: series}))
        self.assertEqual(ctx.exception.status, 422)
        self.assertIn("a-caged-tiger", ctx.exception.detail)

    def test_a_series_page_lists_what_is_there_with_titles_and_sizes(self):
        """The page already says how much is there: title, word count, parts.

        The live shape, from Heart & Soul: a Story/Stories tab whose entries are
        `<h2><a href="...">Title</a> (82,200)</h2>`, a work published in four
        parts listed as one work, and a work whose slug carries -N-M still being
        a work of the series rather than a bundle page of it.
        """
        series = """<html><body><h1 class="entry-title">Heart &#038; Soul (Series)</h1>
<div class="entry-content"><div class="responsive-tabs">
<h2 class="tabtitle">Stories</h2><div class="tabcontent">
<h2><a href="https://keiramarcos.com/2021/06/hearts-desire-1-3/">Heart&#8217;s Desire</a> (82,200)</h2>
<p>Years after the war.</p>
<h2><a href="https://keiramarcos.com/2021/09/soul-magic-2/">Soul Magic</a> (25,127)</h2>
<p>Loch Ness.</p>
<h2><a href="https://keiramarcos.com/2022/10/only-time-1-4/">Only Time</a> (122,430)</h2>
<p>Cast into the past.</p>
</div><h2 class="tabtitle">Casting</h2><div class="tabcontent">
<a href="https://keiramarcos.com/2021/06/hearts-desire-1-3/">Harry</a>
</div></div></div></body></html>"""
        url = "https://keiramarcos.com/fan-fiction/heart-soul-series/"
        items = km.series_items(series, url)
        self.assertEqual([i["title"] for i in items],
                         ["Heart\u2019s Desire", "Soul Magic", "Only Time"])
        self.assertEqual([i["words"] for i in items], [82200, 25127, 122430])
        self.assertEqual([i["parts"] for i in items], [3, None, 4])
        self.assertEqual([i["url"] for i in items],
                         ["https://keiramarcos.com/2021/06/hearts-desire-1-3/",
                          "https://keiramarcos.com/2021/09/soul-magic-2/",
                          "https://keiramarcos.com/2022/10/only-time-1-4/"])
        self.assertEqual(km.series_title(series), "Heart & Soul")

    def test_an_episodes_tab_is_a_series_too(self):
        series = """<html><body><h1 class="entry-title">The Vanguard (Series)</h1>
<div class="entry-content"><div class="responsive-tabs">
<h2 class="tabtitle">Episodes</h2><div class="tabcontent">
<p>1. <a href="https://keiramarcos.com/2021/10/burning-bridges/">Burning Bridges</a> (9,220 words)</p>
<p>2. <a href="https://keiramarcos.com/2021/10/unexpected-reinforcements/">Unexpected Reinforcements</a> (10,739 words)</p>
</div><h2 class="tabtitle">Casting</h2><div class="tabcontent"></div>
</div></div></body></html>"""
        url = "https://keiramarcos.com/fan-fiction/the-vanguard-series/"
        items = km.series_items(series, url)
        self.assertEqual([i["title"] for i in items],
                         ["Burning Bridges", "Unexpected Reinforcements"])
        self.assertEqual([i["words"] for i in items], [9220, 10739])

    def test_a_work_page_lists_no_works_of_its_own(self):
        """One work's Stories tab links its own parts: same family, not a series."""
        self.assertEqual(km.series_items(LANDING, WORK), [])

    def test_the_series_refusal_carries_the_list_to_pick_from(self):
        from host.contracts import Refused
        series = """<html><body><h1 class="entry-title">Hold My Coffee (Series)</h1>
<div class="entry-content"><div class="responsive-tabs">
<h2 class="tabtitle">Stories</h2><div class="tabcontent">
<h2><a href="https://keiramarcos.com/2017/02/a-caged-tiger/">A Caged Tiger</a> (41,000)</h2>
<h2><a href="https://keiramarcos.com/2017/02/an-empty-coffin/">An Empty Coffin</a> (32,000)</h2>
</div></div></div></body></html>"""
        url = "https://keiramarcos.com/fan-fiction/stargate-atlantis-fan-fiction/holdmycoffee/"
        k = km.Keiramarcos()
        with self.assertRaises(Refused) as ctx:
            k.read({"kind": "url", "url": url}, FakeFetch({url: series}))
        ser = ctx.exception.extra["series"]
        self.assertEqual(ser["title"], "Hold My Coffee")
        self.assertEqual(ser["page"], url)
        self.assertEqual([w["title"] for w in ser["works"]],
                         ["A Caged Tiger", "An Empty Coffin"])
        # and the same list is available without triggering the refusal
        self.assertEqual(k.series({"kind": "url", "url": url},
                                  FakeFetch({url: series})),
                         {"title": "Hold My Coffee", "page": url,
                          "works": km.series_items(series, url)})

    def test_series_is_none_for_a_work_page(self):
        self.assertIsNone(km.Keiramarcos().series(
            {"kind": "url", "url": WORK}, FakeFetch(dict(PAGES))))

    # An older shape, still live: no tabs at all, and the one work is linked by
    # WordPress shortlink. Ring of Fire (2011) is the page that caught this.
    OLD_SERIES = """<html><body><h1 class="entry-title">Ring of Fire (series)</h1>
<div class="entry-content"><p>Series Summary: John and Rodney face obstacles.</p>
<ol><li><a title="And the Stars Burn" href="https://keiramarcos.com/?p=2937">And the Stars Burn </a>(32,017 words)<br /></li></ol>
</div></body></html>"""
    OLD_URL = "https://keiramarcos.com/fan-fiction/stargate-atlantis-fan-fiction/ring-of-fire/"
    SHORT = "https://keiramarcos.com/?p=2937"
    CANON = "https://keiramarcos.com/2011/02/ring-of-fire-and-the-stars-burn/"

    def test_a_shortlink_on_a_page_with_no_tabs_is_still_a_work(self):
        items = km.series_items(self.OLD_SERIES, self.OLD_URL)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], self.SHORT)
        self.assertEqual(items[0]["title"], "And the Stars Burn")
        self.assertEqual(items[0]["words"], 32017)

    def test_a_series_holding_one_work_reads_that_work(self):
        page = post("Ring of Fire: And the Stars Burn", ["Chapter One"]).replace(
            "<title>", '<link rel="canonical" href="%s" /><title>' % self.CANON)
        book = km.Keiramarcos().read(
            {"kind": "url", "url": self.OLD_URL},
            FakeFetch({self.OLD_URL: self.OLD_SERIES, self.SHORT: page}))
        self.assertEqual(len(book.chapters), 1)
        self.assertEqual(book.meta["identity"], km.identity_of(self.CANON))

    def test_the_shortlink_is_not_the_name_of_the_book(self):
        """Two ways in are one story: identity comes from the page's own address."""
        page = post("Ring of Fire: And the Stars Burn", ["Chapter One"]).replace(
            "<title>", '<link rel="canonical" href="%s" />\n<title>' % self.CANON)
        book = km.Keiramarcos().read({"kind": "url", "url": self.SHORT},
                                     FakeFetch({self.SHORT: page}))
        self.assertEqual(book.meta["url"], self.CANON)
        self.assertEqual(book.meta["identity"],
                         km.identity_of(self.CANON))
        self.assertNotEqual(book.meta["identity"], km.identity_of(self.SHORT))

    def test_a_work_page_that_mentions_a_sister_story_is_not_a_series(self):
        """No "(Series)" in the title, no following the one link it lists."""
        page = """<html><body><h1 class="entry-title">Shattered</h1>
<div class="entry-content"><p>Thanks for reading.</p>
<ol><li><a href="https://keiramarcos.com/2011/02/ring-of-fire-and-the-stars-burn/">And the Stars Burn </a>(32,017 words)</li></ol>
</div></body></html>"""
        url = "https://keiramarcos.com/fan-fiction/shattered/"
        self.assertFalse(km.series_marked(page))
        self.assertEqual([i["url"] for i in km.series_items(page, url)],
                         ["https://keiramarcos.com/2011/02/ring-of-fire-and-the-stars-burn/"])

    def test_a_stored_copy_from_a_bundle_link_is_the_same_work(self):
        k = km.Keiramarcos()
        self.assertTrue(k.same({"url": P1}, {"url": WORK}))
        self.assertFalse(k.same({"url": WORK},
                                {"url": "https://keiramarcos.com/fan-fiction/daylight/"}))


# ---- an episodic work: the season tab is the story, not a shelf ----------------

EP = "https://keiramarcos.com/fan-fiction/sentinels/"
E1 = "https://keiramarcos.com/2008/08/28/the-gathering-part-one/"
E2 = "https://keiramarcos.com/2010/03/the-sentinel/"

EPISODE_HEADER = """<p>Reading Time: 140 Minutes</p>
<p>Title: %s</p>
<p>Author: Keira Marcos</p>
<p>Series: The Sentinels of Atlantis</p>
<p>Series Order: 1</p>
<p>Pairing: McKay/Sheppard</p>
<p>Warning: explicit sex</p>
<p>Summary:</p>
<p>Sentinel John Sheppard thought he would spend his life unbonded.</p>
<p>Banner Art for the Page by CJ</p>
<p>Series title: The Sentinels of Atlantis</p>
<p>* * * *</p>"""


def episode(title, line):
    return """<html><body><article><h1 class="entry-title">%s</h1>
<div class="entry-content">%s
<p>%s</p></div></article></body></html>""" % (title, EPISODE_HEADER % title, line)


EPISODIC = """<html><body>
<h1 class="entry-title">The Sentinels of Atlantis (Series)</h1>
<div class="entry-content"><p>The story of the Sentinels of Atlantis.</p>
<div class="responsive-tabs">
<h2 class="tabtitle">Season 1</h2>
<div class="tabcontent">
<p><a href="%s">The Gathering</a></p>
<p><a href="%s">The Sentinel</a></p>
</div>
<h2 class="tabtitle">The Cast</h2>
<div class="tabcontent"><a href="https://keiramarcos.com/fan-fiction/sentinels/joan/">Joan</a></div>
</div></div></body></html>""" % (E1, E2)


class TestEpisodic(unittest.TestCase):
    def fetch(self):
        return FakeFetch({EP: EPISODIC,
                          E1: episode("The Gathering", "There is an unbonded Guide here."),
                          E2: episode("The Sentinel", "Dean Bates sat down on his footlocker.")})

    def test_a_season_tab_is_one_book_read_in_order(self):
        book = km.Keiramarcos().read({"kind": "url", "url": EP}, self.fetch())
        self.assertEqual(book.title, "The Sentinels of Atlantis")
        self.assertEqual([c.title for c in book.chapters], ["The Gathering", "The Sentinel"])
        self.assertEqual(book.meta["parts"], 2)

    def test_a_day_dated_permalink_is_a_post(self):
        self.assertTrue(km.is_post_url(E1))
        self.assertTrue(km.is_post_url("https://keiramarcos.com/2024/03/a-glimpse-of-us-1-2/"))

    def test_every_episode_loses_its_own_metadata_block(self):
        book = km.Keiramarcos().read({"kind": "url", "url": EP}, self.fetch())
        for ch in book.chapters:
            self.assertNotIn("Title:", ch.text)
            self.assertNotIn("Series Order", ch.text)
            self.assertNotIn("Reading Time", ch.text)
            self.assertNotIn("Banner Art", ch.text)
            self.assertNotIn("* * * *", ch.text)
            self.assertNotIn("Author: Keira Marcos", ch.text)
        self.assertTrue(book.chapters[1].text.startswith("Dean Bates"))

    def test_the_book_counts_its_own_words_not_one_episodes(self):
        book = km.Keiramarcos().read({"kind": "url", "url": EP}, self.fetch())
        self.assertNotEqual(book.meta["word_count"], "140")
        total = sum(len(c.text.split()) for c in book.chapters)
        self.assertEqual(book.meta["word_count"], "{:,}".format(total))


# ---- a story published one post per chapter -----------------------------------
#
# The live shape, from No Enemy Within: the work's own index page is a PAGE
# titled with the work, and it lists the chapters as shortlinks labelled
# "Chapter One", "Chapter Six", ... in two columns, so the page order runs
# One, Six, Two, Seven. Nothing in the URL says these are chapters of one work:
# every chapter is its own dated post with its own slug. Imported as a list of
# works, this shelf got ten books of one chapter each.

INDEX_WORK = ("https://keiramarcos.com/fan-fiction/stargate-atlantis-fan-fiction/"
              "the-lantean-legacy/lantean-legacy-no-enemy-within/")


def chapter_link(p, label):
    return '<p><a href="https://keiramarcos.com/?p=%d">%s</a></p>' % (p, label)


INDEX_PAGE = """<html><head><title>No Enemy Within &#8211; Keira Marcos</title>
<link rel="canonical" href="%s" /></head><body>
<h1 class="entry-title">No Enemy Within</h1>
<div class="entry-content"><p>Reading Time: 1 Minutes</p>
<p>Art by FanArts Series</p>
%s
<p><a href="https://keiramarcos.com/?page_id=2266">Return to the Series Page</a></p>
</div></body></html>""" % (INDEX_WORK, "".join([
    chapter_link(2408, "Chapter One"),
    chapter_link(2437, "Chapter Three"),
    chapter_link(2413, "Chapter Two"),
    chapter_link(2442, "Chapter Four"),
]))


def chapter_post(label, p):
    return """<html><body><h1 class="entry-title">No Enemy Within &#8211; %s</h1>
<div class="entry-content"><p>Art by FanArts Series</p>
<p>%s body text, long enough to be read aloud, and with no chapter heading of
its own: this post is one chapter of the story and says so only in its title.
The reader has to keep that whole body as one chapter rather than guessing.</p>
</div></body></html>""" % (label, label)


CHAPTER_PAGES = {
    "https://keiramarcos.com/?p=2408": chapter_post("Chapter One", 2408),
    "https://keiramarcos.com/?p=2413": chapter_post("Chapter Two", 2413),
    "https://keiramarcos.com/?p=2437": chapter_post("Chapter Three", 2437),
    "https://keiramarcos.com/?p=2442": chapter_post("Chapter Four", 2442),
}


class TestStoryIndex(unittest.TestCase):
    """A page that lists chapters is one book, not a shelf of one-chapter books."""

    def read_index(self, url="https://keiramarcos.com/?page_id=2404"):
        pages = dict(CHAPTER_PAGES)
        pages[url] = INDEX_PAGE
        fetch = FakeFetch(pages)
        return km.Keiramarcos().read({"kind": "url", "url": url}, fetch), fetch

    def test_the_ordinal_in_a_label_is_read_off_the_words(self):
        self.assertEqual(km.chapter_ordinal("Chapter One"), 1)
        self.assertEqual(km.chapter_ordinal("Chapter Ten"), 10)
        self.assertEqual(km.chapter_ordinal("Ch. 7"), 7)
        self.assertEqual(km.chapter_ordinal("Part 2"), 2)
        self.assertIsNone(km.chapter_ordinal("Epilogue"))
        self.assertIsNone(km.chapter_ordinal("Heart&#8217;s Desire"))
        self.assertIsNone(km.chapter_ordinal("1. A Caged Tiger"))

    def test_a_page_of_chapters_is_one_book_in_chapter_order(self):
        book, _ = self.read_index()
        self.assertEqual(book.title, "No Enemy Within")
        self.assertEqual([c.title for c in book.chapters],
                         ["Chapter One", "Chapter Two", "Chapter Three", "Chapter Four"])
        self.assertEqual(book.meta["parts"], 4)

    def test_reading_order_comes_from_the_label_not_the_layout(self):
        # the fixture lists One, Three, Two, Four: page order is a lie
        book, _ = self.read_index()
        self.assertEqual([c.text.split(" body")[0] for c in book.chapters],
                         ["Chapter One", "Chapter Two", "Chapter Three", "Chapter Four"])

    def test_the_book_is_named_by_the_address_the_page_declares(self):
        book, _ = self.read_index()
        self.assertEqual(book.meta["url"], INDEX_WORK)
        self.assertEqual(book.meta["identity"], km.identity_of(INDEX_WORK))
        # the shortlink entry page and the pretty address are the same book
        pretty, _ = self.read_index(INDEX_WORK)
        self.assertEqual(pretty.meta["identity"], book.meta["identity"])

    def test_an_index_page_never_offers_a_series_picker(self):
        pages = dict(CHAPTER_PAGES)
        pages[INDEX_WORK] = INDEX_PAGE
        self.assertIsNone(km.Keiramarcos().series(
            {"kind": "url", "url": INDEX_WORK}, FakeFetch(pages)))

    def test_the_art_credit_is_not_read_as_the_first_line(self):
        book, _ = self.read_index()
        for ch in book.chapters:
            self.assertFalse(ch.text.startswith("Art by"), ch.text[:40])

    def test_a_series_listing_is_not_mistaken_for_a_chapter_index(self):
        series = """<html><body><h1 class="entry-title">Hold My Coffee (Series)</h1>
<div class="entry-content">
<h2><a href="https://keiramarcos.com/2017/02/a-caged-tiger/">1. A Caged Tiger</a></h2>
<h2><a href="https://keiramarcos.com/2017/02/an-empty-coffin/">2. An Empty Coffin</a></h2>
</div></body></html>"""
        url = "https://keiramarcos.com/fan-fiction/x/holdmycoffee/"
        self.assertEqual(km.chapter_index_items(series, url), [])



# ---- a work published under spelled parts, linked by hand ----------------------
# Live shape (When You Kiss Me, 2008): four posts with no work page and no tab,
# /2008/09/when-you-kiss-me-part-one/ .. -part-four/. The last part links only
# its previous neighbour, and its body ends with links to the two stories that
# follow it in the series. Both facts used to break the import: the reader read
# the body links as a series and offered February Song and the Iris March
# interview instead of this book, and a paste of part four came back as one
# chapter because nothing said part three was part of the same work.

SPELLED = "https://keiramarcos.com/fan-fiction/when-you-kiss-me/"
SP = ["https://keiramarcos.com/2008/09/when-you-kiss-me-part-%s/" % w
      for w in ("one", "two", "three", "four")]
SISTER = ["https://keiramarcos.com/2008/09/05/february-song/",
          "https://keiramarcos.com/2009/11/04/an-interview/"]


def spelled_part(title, canon, prev_url="", next_url="", sisters=()):
    """One part page: the part link sits in the post navigation outside the body,
    which is where WordPress puts it, and the body ends with sister links."""
    def nav(href, label):
        return ('<div class="nav-previous"><a href="%s" rel="prev">%s</a></div>'
                % (href, label)) if href else ""
    tail = "".join('<p>Read <a href="%s">%s</a> next.</p>' % (u, u.rsplit("/", 2)[-2])
                   for u in sisters)
    return """<html><head><link rel="canonical" href="%s" /></head><body>
<article><h1 class="entry-title">%s</h1>
<div class="entry-content"><p>Reading Time: 43 Minutes</p><p>* * * *</p>
<p>%s body text, long enough to be read aloud on the bench, which is the only
thing this paragraph has to do: clear the floor below which a page counts as
empty and nothing was read.</p>%s</div>
%s%s</article></body></html>""" % (canon, title, title, tail,
                                   nav(prev_url, "Previous"),
                                   nav(next_url, "Next"))


SPELLED_PAGES = {
    SP[0]: spelled_part("When You Kiss Me \u2013 Part One", SP[0], next_url=SP[1]),
    SP[1]: spelled_part("When You Kiss Me \u2013 Part Two", SP[1], prev_url=SP[0], next_url=SP[2]),
    SP[2]: spelled_part("When You Kiss Me- Part Three", SP[2], prev_url=SP[1], next_url=SP[3]),
    SP[3]: spelled_part("When You Kiss Me- Part Four", SP[3], prev_url=SP[2], sisters=SISTER),
}
# the site answers a wrong /fan-fiction/ guess with a real post rather than a
# 404, which is how a paste of part four used to be replaced by the guess
SPELLED_PAGES[SPELLED] = SPELLED_PAGES[SP[3]]


class TestSpelledParts(unittest.TestCase):
    def test_a_spelled_part_is_a_part_of_one_work(self):
        self.assertEqual(km.split_part("when-you-kiss-me-part-four"),
                         ("when-you-kiss-me", 4, None))
        self.assertEqual(km.identity_of(SP[3]), km.identity_of(SP[0]))
        self.assertNotEqual(km.identity_of(SP[0]),
                            km.identity_of("https://keiramarcos.com/2008/09/february-song/"))

    def test_pasting_the_last_part_gets_the_whole_work(self):
        book = km.Keiramarcos().read({"kind": "url", "url": SP[3]},
                                     FakeFetch(dict(SPELLED_PAGES)))
        self.assertEqual(book.meta["parts"], 4)
        self.assertEqual([c.title for c in book.chapters],
                         ["When You Kiss Me \u2013 Part One", "When You Kiss Me \u2013 Part Two",
                          "When You Kiss Me- Part Three", "When You Kiss Me- Part Four"])
        self.assertEqual(book.meta["identity"], km.identity_of(SP[0]))

    def test_the_two_stories_linked_from_the_body_are_not_the_work(self):
        from host.contracts import Refused
        try:
            km.Keiramarcos().read({"kind": "url", "url": SP[3]},
                                  FakeFetch(dict(SPELLED_PAGES)))
        except Refused as e:                       # the old behaviour: a shelf of two
            self.fail("a post that links sister stories was read as a series: %s" % e)

    def test_every_part_is_the_same_book_however_it_is_pasted(self):
        k = km.Keiramarcos()
        for url in SP:
            book = k.read({"kind": "url", "url": url}, FakeFetch(dict(SPELLED_PAGES)))
            self.assertEqual(book.meta["parts"], 4, url)
            self.assertEqual(book.meta["identity"], km.identity_of(SP[0]), url)

    def test_a_multi_part_work_with_no_total_counts_its_own_words(self):
        book = km.Keiramarcos().read({"kind": "url", "url": SP[0]},
                                     FakeFetch(dict(SPELLED_PAGES)))
        total = sum(len(c.text.split()) for c in book.chapters)
        self.assertEqual(book.meta["word_count"], "{:,}".format(total))

    def test_a_wrong_landing_guess_that_lands_on_a_post_is_ignored(self):
        """/fan-fiction/when-you-kiss-me/ serves part four, not a work page.

        The site answers a wrong /fan-fiction/ guess with a real post instead of
        a 404, so the guess has to be checked: a work page is a WordPress page,
        and a canonical that is a dated post means this was never one.
        """
        self.assertEqual(km.canonical_of(SPELLED_PAGES[SPELLED]), SP[3])
        book = km.Keiramarcos().read({"kind": "url", "url": SP[0]},
                                     FakeFetch(dict(SPELLED_PAGES)))
        self.assertEqual(book.meta["url"], SP[0])


class TestOrderedParts(unittest.TestCase):
    def test_a_spelled_run_is_ordered_and_must_be_whole(self):
        found = {(4, None): "d", (3, None): "c", (2, None): "b"}
        self.assertEqual(km.ordered_parts(found, "x"), ["b", "c", "d"])

    def test_a_numbered_run_still_wins_on_the_most_complete_group(self):
        found = {(1, 3): "a", (2, 3): "b", (3, 3): "c", (1, 2): "a2"}
        self.assertEqual(km.ordered_parts(found, "x"), ["a", "b", "c"])


if __name__ == "__main__":
    unittest.main()