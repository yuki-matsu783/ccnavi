"""閉じた親チケットのコピー（`tickets/donecopy.py`）から欄を読む処理を確かめる。"""

from __future__ import annotations

import unittest

from ccnavi.tickets import donecopy


class CopyFieldsTest(unittest.TestCase):
    def test_frontmatter_values_are_read_without_the_body(self):
        copy = donecopy.copy_fields("---\nticket: i0001\n---\nticket: i0002\n")
        self.assertEqual("i0001", copy.ticket)

    def test_values_are_stripped_and_missing_ones_are_empty(self):
        copy = donecopy.copy_fields("---\nticket: ' i0001-01-01 '\nparent: i0001\n---\n")
        self.assertEqual(("i0001-01-01", "i0001"), (copy.ticket, copy.parent))
        self.assertEqual("", donecopy.copy_fields("---\nticket: i0001\n---\n").parent)

    def test_an_unreadable_frontmatter_is_none(self):
        self.assertIsNone(donecopy.copy_fields("no frontmatter\n"))


if __name__ == "__main__":
    unittest.main()
