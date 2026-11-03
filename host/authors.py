"""One author, one key.

Names arrive as whatever the epub said: "B. Arthur", "B Arthur", "Arthur, B.",
"Ursula K. Le Guin". Nothing in the library ever wrote them, so every list that
groups or matches by author got a different bucket for the same person. This
turns a display name into a comparison key.

The key is for matching only. The shelf still shows the spelling on the book;
this exists so two of those spellings land in one bucket instead of two.
"""

import re
import unicodedata

# Anything that is not a letter or a digit is a separator: periods in initials,
# commas in "Last, First", ampersands, apostrophes in O'Brien.
_SEP = re.compile(r"[^0-9a-z]+")
_INITIAL_RUN = re.compile(r"(?:\b[a-z]\s+)+[a-z]\b")


def author_key(name):
    """A stable comparison key for an author name. Never shown to anyone.

    - case folded and accent folded (Brontë and Bronte are one person)
    - punctuation dropped, so "B. Arthur" == "B Arthur"
    - "Arthur, B." flipped to "b arthur" before punctuation goes
    - runs of single letters joined, so "J. R. R. Tolkien" == "J.R.R. Tolkien"
    """
    raw = unicodedata.normalize("NFKD", str(name or ""))
    raw = "".join(c for c in raw if not unicodedata.combining(c))
    raw = raw.casefold()

    # "Last, First" -> "First Last". Only one comma, and only when both sides
    # have something in them: two commas is "Smith, John, Jr" and guessing is
    # worse than leaving it.
    parts = raw.split(",")
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        raw = parts[1].strip() + " " + parts[0].strip()

    # keep '&' as the word it stands for before everything else is stripped
    raw = raw.replace("&", " and ")

    words = [w for w in _SEP.split(raw) if w]
    if not words:
        return ""
    key = " ".join(words)
    # "j r r tolkien" -> "jrr tolkien"
    return _INITIAL_RUN.sub(lambda m: m.group(0).replace(" ", ""), key)
