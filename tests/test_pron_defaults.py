"""The starter dictionary: every reader inherits a set of name fixes once.

The words English voices mangle most are place names, and nobody types a fix
for a name they have not reached yet, so it has to be waiting for them. It is
an ordinary dictionary, not a hidden layer: these tests are about it behaving
like one, and about it never coming back after a reader deletes it.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from host import db, pron, pron_defaults


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fireside-defaults-")
        self.conn = db.connect(os.path.join(self.tmp, "d.db"))
        self.uid = "u1"

    def tearDown(self):
        try:
            self.conn.close()
        except Exception:
            pass

    def builtin(self):
        for d in pron.dicts(self.conn, self.uid):
            if d["name"] == pron_defaults.DEFAULTS_NAME:
                return d
        return None


class Inherit(Base):
    def test_a_new_reader_has_the_set(self):
        d = self.builtin()
        self.assertIsNotNone(d)
        self.assertEqual(d["scope"], "global")
        words = {r["from"].lower() for r in d["rules"]}
        self.assertIn("los angeles", words)
        self.assertIn("thames", words)
        self.assertEqual(len(d["rules"]), len(pron_defaults.RULES))

    def test_seeding_happens_once(self):
        first = pron.dicts(self.conn, self.uid)
        again = pron.dicts(self.conn, self.uid)
        named = [d for d in again if d["name"] == pron_defaults.DEFAULTS_NAME]
        self.assertEqual(len(named), 1)
        self.assertEqual(len(first), len(again))

    def test_it_actually_fixes_the_name_every_engine_sees(self):
        rules = pron.load(self.conn, self.uid)
        self.assertEqual(pron.spoken("Los Angeles, then home.", rules),
                         "Los Anjeles, then home.")
        # the shipped rule is whole-word, so it does not eat a longer word
        self.assertEqual(pron.spoken("Los Angelese", rules), "Los Angelese")

    def test_case_is_not_a_requirement(self):
        rules = pron.load(self.conn, self.uid)
        self.assertEqual(pron.spoken("LOS ANGELES", rules), "Los Anjeles")


class Editable(Base):
    def test_a_reader_can_change_the_shipped_respelling(self):
        d = self.builtin()
        rules = [dict(r) for r in d["rules"]]
        for r in rules:
            if r["from"].lower() == "los angeles":
                r["to"] = "Los Anjuhless"
        pron.save_dict(self.conn, self.uid, d["name"], "global", "", rules,
                       dict_id=d["id"])
        self.assertEqual(pron.spoken("Los Angeles", pron.load(self.conn, self.uid)),
                         "Los Anjuhless")

    def test_a_reader_can_delete_it_and_it_stays_deleted(self):
        d = self.builtin()
        pron.delete_dict(self.conn, self.uid, d["id"])
        self.assertIsNone(self.builtin())
        # a later read, which is when seeding would run again if it could
        pron.load(self.conn, self.uid)
        self.assertIsNone(self.builtin())
        self.assertEqual(pron.spoken("Los Angeles", pron.load(self.conn, self.uid)),
                         "Los Angeles")

    def test_a_narrower_rule_wins_over_the_shipped_one(self):
        self.conn.execute(
            "INSERT INTO books(id,user_id,title,author) VALUES(?,?,?,?)",
            ("b1", self.uid, "A Book", "Someone"))
        self.conn.commit()
        pron.save_dict(self.conn, self.uid, "This book", "book", "b1",
                       [{"from": "Los Angeles", "to": "El Lay"}])
        rules = pron.for_book(pron.load(self.conn, self.uid), "b1", "Someone", ())
        self.assertEqual(pron.spoken("Los Angeles", rules), "El Lay")
        # and it does not leak to another book
        other = pron.for_book(pron.load(self.conn, self.uid), "b2", "Someone", ())
        self.assertEqual(pron.spoken("Los Angeles", other), "Los Anjeles")


class NoAccount(unittest.TestCase):
    def test_an_empty_user_is_left_alone(self):
        tmp = tempfile.mkdtemp(prefix="fireside-defaults-")
        conn = db.connect(os.path.join(tmp, "d.db"))
        try:
            self.assertEqual(pron.load(conn, ""), [])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
