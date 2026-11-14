"""One footer on every page, with the same five links.

The foot is the only place a reader can find the refund policy, and it drifted
page by page: /plans served the shell's foot without Refunds while the legal
pages carried it. This checks the source files themselves, because "we put it
on every page" is exactly the claim that goes quietly false.
"""

import os
import re
import unittest

UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui")

LINKS = ["/about", "/contact", "/terms", "/refunds", "/privacy"]


def _foot_block(html):
    """The <footer> or <nav class="foot"> block, whichever the page uses."""
    for pattern in (r"<footer[^>]*class=\"foot\"[^>]*>(.*?)</footer>",
                    r"<nav[^>]*class=\"foot\"[^>]*>(.*?)</nav>"):
        m = re.search(pattern, html, re.S)
        if m:
            return m.group(1)
    return None


class TestFooterConsistency(unittest.TestCase):
    def pages(self):
        for name in sorted(os.listdir(UI)):
            if name.endswith(".html"):
                with open(os.path.join(UI, name), encoding="utf-8") as fh:
                    yield name, fh.read()

    def test_every_page_carries_the_same_five_links_in_order(self):
        for name, html in self.pages():
            with self.subTest(page=name):
                block = _foot_block(html)
                self.assertIsNotNone(block, f"{name} has no foot")
                hrefs = re.findall(r'href="([^"]+)"', block)
                self.assertEqual(hrefs, LINKS, f"{name} foot drifted: {hrefs}")

    def test_refunds_page_is_linked_from_the_app_shell(self):
        with open(os.path.join(UI, "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn('href="/refunds"', html,
                      "the app shell (and so /plans) has no Refunds link")


if __name__ == "__main__":
    unittest.main()
