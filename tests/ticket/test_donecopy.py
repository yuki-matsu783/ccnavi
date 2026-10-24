"""閉じた親チケットのコピー（`tickets/donecopy.py`）。同じ親かを決める欄の順と、欄の読み方。"""

from __future__ import annotations

import unittest

from ccnavi.tickets import donecopy


class SameParentTest(unittest.TestCase):
    def test_the_order_of_fields(self):
        copy = donecopy.DoneCopy
        mine = copy("i0001", "", base_sha="", started_at="t1", cancelled_at="write-and-push")
        self.assertTrue(donecopy.same_parent(mine, copy("i0001", "", started_at="t1")))
        self.assertFalse(donecopy.same_parent(mine, copy("i0001", "", started_at="t2")))
        # started_at が片方にしか無ければ、次の cancelled_at で比べる。
        unstarted = copy("i0001", "", cancelled_at="write-and-push")
        self.assertTrue(
            donecopy.same_parent(unstarted, copy("i0001", "", cancelled_at="write-and-push"))
        )
        self.assertTrue(donecopy.same_parent(mine, unstarted))
        self.assertFalse(donecopy.same_parent(mine, copy("i0001", "", cancelled_at="c2")))

    def test_nothing_to_compare_is_not_the_same(self):
        copy = donecopy.DoneCopy
        # 手元に親が無いとき、空どうしのとき（どの欄でも照合できない）は同じとしない。
        self.assertFalse(
            donecopy.same_parent(None, copy("i0001", "", cancelled_at="write-and-push"))
        )
        self.assertFalse(donecopy.same_parent(copy("i0001", ""), copy("i0001", "")))

    def test_the_first_field_both_have_decides(self):
        copy = donecopy.DoneCopy
        mine = copy("i0001", "", base_sha="aaa", started_at="t1")
        other = copy("i0001", "", base_sha="bbb", started_at="t1")
        self.assertFalse(donecopy.same_parent(mine, other))


class CopyFieldsTest(unittest.TestCase):
    def test_frontmatter_values_are_read_without_the_body(self):
        copy = donecopy.copy_fields("---\nticket: i0001\nbase_sha: ''\n---\nbase_sha: abc\n")
        self.assertEqual("", copy.base_sha)
        self.assertEqual("i0001", copy.ticket)

    def test_values_are_stripped_and_missing_ones_are_empty(self):
        copy = donecopy.copy_fields(
            "---\nticket: i0001-01-01\nparent: i0001\nstarted_at: ' t1 '\n---\n"
        )
        self.assertEqual(
            ("i0001-01-01", "i0001", "t1"), (copy.ticket, copy.parent, copy.started_at)
        )
        self.assertEqual(("", ""), (copy.base_sha, copy.cancelled_at))

    def test_an_unreadable_frontmatter_is_none(self):
        self.assertIsNone(donecopy.copy_fields("no frontmatter\n"))


if __name__ == "__main__":
    unittest.main()
