"""範囲の glob に空白があるとき（issue #785）。

空白区切りで複数のパスを 1 項目に書いた提案は、1 つの空白入りファイル名にしか一致せず、
承認しても編集が全部止まる。lint・`approval-request`・承認画面が共通で通る `ticket.parse` で
error にし、承認画面の範囲は項目ごとの箇条書きにする。
"""

from __future__ import annotations

import unittest

from ccnavi.policy import rules
from ccnavi.tickets.model import ticket as ticket_mod
from ccnavi.tickets.proposal import agree_screen
from tests.ticketsample import child_text


def errors_of(allow):
    _, problems = ticket_mod.parse(child_text("feature-1-memo-01-01", "feature-1-memo", 1, allow))
    return [p for p in problems if p.severity == rules.SEVERITY_ERROR]


class ScopeGlobWhitespaceTest(unittest.TestCase):
    def test_space_separated_single_entry_is_error(self):
        found = errors_of(["textkit/words.py textkit/__init__.py"])
        self.assertTrue(any("空白がある" in p.detail for p in found), found)

    def test_one_pattern_per_entry_passes(self):
        self.assertEqual(errors_of(["textkit/words.py", "textkit/__init__.py"]), [])


class BulletsTest(unittest.TestCase):
    def test_one_line_per_item(self):
        self.assertEqual(
            agree_screen._bullets("allow", ["a/*", "b/*"]),
            ["    allow:", "      - a/*", "      - b/*"],
        )

    def test_empty_prints_nothing(self):
        self.assertEqual(agree_screen._bullets("deny", []), [])


if __name__ == "__main__":
    unittest.main()
