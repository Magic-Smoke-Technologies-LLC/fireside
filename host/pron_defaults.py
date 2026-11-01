"""The starter dictionary every reader inherits.

English place names are the words a voice gets wrong most often and that a
person notices immediately: "Los Angeleez". Almost nobody types a fix for a
name they have not hit yet, so the fix has to arrive before them, as an
ordinary dictionary they can read, edit, delete, or export like any other.

It ships once per account, as a global dict named DEFAULTS_NAME. Deleting it is
respected: the seeded flag stays set, so it never comes back on its own.

The respellings are first guesses at what the two voices will actually say, not
measurements. Each line is editable for exactly that reason, and the one that
is wrong is a person's to correct, not ours to defend.
"""

DEFAULTS_NAME = "Common fixes (built in)"

# Bumped if we ever want to offer a newer set to accounts that already have
# the old one. Seeding only checks "not zero", so a bump alone re-seeds nothing.
DEFAULTS_VERSION = 1

RULES = [
    # The one that started this: -es read as -eez.
    {"from": "Los Angeles", "to": "Los Anjeles"},
    {"from": "Los Angelus", "to": "Los Anjeles"},
    # Silent letters and non-obvious stress.
    {"from": "Thames", "to": "Temz"},
    {"from": "Worcester", "to": "Wooster"},
    {"from": "Gloucester", "to": "Gloster"},
    {"from": "Leicester", "to": "Lester"},
    {"from": "Edinburgh", "to": "Edinbruh"},
    {"from": "Boise", "to": "Boysee"},
    {"from": "Sioux", "to": "Soo"},
    {"from": "Tucson", "to": "Tooson"},
    {"from": "Yosemite", "to": "Yo-sem-ih-tee"},
    {"from": "Poughkeepsie", "to": "Po-kip-see"},
    # Names an English-trained voice reads off a foreign spelling.
    {"from": "Des Moines", "to": "De Moin"},
    {"from": "Nguyen", "to": "Win"},
    {"from": "Louisville", "to": "Looavul"},
]


def rules():
    """A fresh copy, so a caller editing the result cannot edit the defaults."""
    return [dict(r) for r in RULES]
