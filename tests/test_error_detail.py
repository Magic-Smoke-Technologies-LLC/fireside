"""A refused request carries a sentence, not our stack trace.

The failure this pins: a 504-then-retry add raised OperationalError, and the
repair path returned detail=str(e)[:300], so the screen printed
"OperationalError: SAVEPOINT addbook does not exist" as user-facing copy.
"""
import unittest

from host.app import _detail


class TestDetailForReader(unittest.TestCase):
    def test_exception_class_name_never_reaches_the_reader(self):
        class OperationalError(Exception):
            pass
        e = OperationalError("SAVEPOINT addbook does not exist")
        self.assertEqual(_detail(e, "that page would not open"),
                         "that page would not open")

    def test_a_written_refusal_passes_through(self):
        # host/auth.py and the readers refuse in ValueError with a real sentence
        self.assertEqual(_detail(ValueError("this page does not list works of its own"),
                                 "nope"),
                         "this page does not list works of its own")

    def test_a_multiline_message_is_not_copy(self):
        self.assertEqual(_detail(ValueError("line one\nline two"), "try again"),
                         "try again")

    def test_an_empty_message_falls_back(self):
        self.assertEqual(_detail(ValueError("   "), "try again"), "try again")


if __name__ == "__main__":
    unittest.main()
