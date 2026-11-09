"""v2 reader plugin: web. Any public http/https link is one chapter, unless the
page is a table of contents for a story split across numbered posts.

The fallback reader, so its manifest declares the highest `order`. It carries
no site knowledge; the page -> container reading it needs lives in
host/textutil.py, shared with every reader that reads article pages.

A hub page is common on hand-built author sites: one short post titled after
the work, holding an ordered list of links to the chapters (each chapter its
own post). Read as one page it is a title page and nothing else, which is what
a reader pasting it actually gets. So: if a page has almost no prose of its own
and carries a clean 1..N run of numbered or "Chapter N" entries, follow them in
order and return one book. Anything less tidy is left alone; the rules are
strict on purpose, because a wrong guess here turns one page into a crawl.
"""
import os
import re
import sys
import urllib.parse

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from host.contracts import to_book
from host.textutil import (best_fragment, clean_fragment, first_heading,
                           page_title, split_meta_header, strip_furniture)

MAX_INDEX_ENTRIES = 80      # a story, not a sitemap
MIN_INDEX_ENTRIES = 3
MAX_INDEX_PROSE = 3000      # a hub's own words are a title and a blurb

_BLOCK = re.compile(r"(?is)<(p|li|h[1-6])\b[^>]*>(.*?)</\1\s*>")
_ANCHOR = re.compile(r"""(?is)<a\b[^>]*?\bhref\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a>""")
_NUMBERED = re.compile(r"^\s*\(?(\d{1,3})[.)](?=\s)")
_LABELLED = re.compile(
    r"(?i)^\s*(?:chapter|part|book|episode|section|post)\s+"
    r"(\d{1,3}|[ivxlcdm]{1,7}|[a-z]{3,9})\b")
_WORDNUM = {w: i for i, w in enumerate(
    ("one two three four five six seven eight nine ten eleven twelve thirteen "
     "fourteen fifteen sixteen seventeen eighteen nineteen twenty").split(), 1)}

_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7,
          "viii": 8, "ix": 9, "x": 10, "xi": 11, "xii": 12, "xiii": 13,
          "xiv": 14, "xv": 15, "xvi": 16, "xvii": 17, "xviii": 18, "xix": 19,
          "xx": 20}


def parse_web(page: str, url: str) -> dict:
    """A fetched page -> {'title','chapters'}. One page is its own chapter."""
    title = page_title(page) or "Untitled"
    text = best_fragment(page)
    if len(text) < 120:
        raise ValueError("no readable text on that page (login wall, or it builds itself in JS)")
    ch = first_heading(page)[:120]
    return {"title": title, "chapters": [{"title": ch or title[:120] or "Page", "text": text}]}


def _host(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()


def _same_host(url: str, base: str) -> bool:
    h = _host(url)
    if not h or h != _host(base):
        return False
    return urllib.parse.urlparse(url).scheme in ("http", "https")


def _clean(url: str, base: str) -> str:
    """Absolute, same host, no query or fragment: the chapter, not the share link."""
    link = urllib.parse.urljoin(base, (url or "").strip())
    u = urllib.parse.urlparse(link)
    if not _same_host(link, base):
        return ""
    return urllib.parse.urlunparse(u._replace(query="", fragment=""))


def _entry_number(text: str):
    """The position this block claims in the run, or None if it claims none."""
    m = _NUMBERED.match(text or "")
    if m:
        return int(m.group(1))
    m = _LABELLED.match(text or "")
    if m:
        raw = m.group(1).lower()
        if raw.isdigit():
            return int(raw)
        return _ROMAN.get(raw) or _WORDNUM.get(raw)
    return None


def index_entries(page: str, url: str) -> list:
    """[{'n','title','urls'}] when this page is a table of contents, else [].

    One block per entry (a paragraph, list item or heading), each carrying a
    position and at least one same-host link. The run must start at 1 and climb
    by one with no gaps: that is what makes this a contents page and not a post
    that happens to link three other posts. A hub entry that lists two links
    ("Part I | Part II") keeps both, in page order.
    """
    body = strip_furniture(page)
    prose = best_fragment(page)
    if len(prose) > MAX_INDEX_PROSE:
        return []                     # a chapter that links onwards, not a contents page
    here = canonical(url)
    entries, seen = [], set()
    for m in _BLOCK.finditer(body):
        text = clean_fragment(m.group(2))
        n = _entry_number(text)
        if n is None:
            continue
        urls = []
        for a in _ANCHOR.finditer(m.group(2)):
            link = _clean(a.group(1), url)
            if not link or link == here or link in seen:
                continue
            seen.add(link)
            urls.append(link)
        if not urls:
            continue
        title = _NUMBERED.sub("", _LABELLED.sub("", text)).strip(" .:-–—")
        entries.append({"n": n, "title": title[:120], "urls": urls})
        if len(entries) > MAX_INDEX_ENTRIES:
            return []
    if len(entries) < MIN_INDEX_ENTRIES:
        return []
    for i, e in enumerate(entries):
        if e["n"] != i + 1:
            return []                 # out of order, or a gap: not a contents page
    return entries


def canonical(url: str) -> str:
    """The same page, spelled one way: no fragment, no trailing slash, lower
    host. Query kept (it often names the story), case kept on the path."""
    u = urllib.parse.urlparse((url or "").strip())
    path = (u.path or "/").rstrip("/") or "/"
    return urllib.parse.urlunparse(
        (u.scheme.lower(), (u.netloc or "").lower(), path, "", u.query, ""))


def identity_of(url: str) -> str:
    return "web:" + canonical(url)


class Web:
    id = "web"
    label = "Web page"

    def matches(self, req) -> bool:
        if req.get("kind") != "url":
            return False
        return urllib.parse.urlparse(req.get("url") or "").scheme in ("http", "https")

    def read(self, req, fetch, cred=None) -> dict:
        url = req["url"]
        page = fetch(url, cred=cred, credential_hosts=self.credential_hosts())
        book = self._read_index(page, url, fetch, cred) or parse_web(page, url)
        book["meta"] = dict(book.get("meta") or {},
                            source=self.id, url=url, identity=identity_of(url))
        return to_book(book)

    def _read_index(self, page: str, url: str, fetch, cred):
        """The book behind a contents page, or None when the page is not one."""
        entries = index_entries(page, url)
        if not entries:
            return None
        chapters, meta = [], {}
        for e in entries:
            for part_url in e["urls"]:
                try:
                    part = fetch(part_url, cred=cred,
                                 credential_hosts=self.credential_hosts())
                except Exception:
                    continue          # a dead entry is one missing chapter, not no book
                text = best_fragment(part)
                if len(text) < 120:
                    continue
                fields, body = split_meta_header(text)
                if fields and not meta:
                    for k in ("Title", "Author", "Summary"):
                        if fields.get(k):
                            meta[k.lower()] = fields[k]
                title = first_heading(part)[:120] or e["title"] or "Chapter"
                chapters.append({"title": title, "text": body})
            if len(chapters) > MAX_INDEX_ENTRIES:
                break
        if len(chapters) < 2:
            return None               # one followable entry is not a contents page
        # The hub's own heading names the work; the first chapter's title names
        # only chapter one, which is how a shelf ends up full of part ones.
        title = first_heading(page) or page_title(page) or meta.get("title") or "Untitled"
        book = {"title": title[:200],
                "chapters": chapters,
                "meta": {"index": len(chapters)}}
        if meta.get("author"):
            book["meta"]["author"] = meta["author"]
        if meta.get("summary"):
            book["meta"]["summary"] = meta["summary"]
        return book

    def same(self, stored, incoming) -> bool:
        """Is the book already on the shelf the same page as this import?

        Books imported before readers named their identity carry only a url, so
        this is what lets a plain re-paste replace the copy instead of stacking
        a second one."""
        a, b = (stored or {}).get("url"), (incoming or {}).get("url")
        return bool(a and b and canonical(a) == canonical(b))

    def settings(self):
        return []

    def credential_hosts(self):
        return []
