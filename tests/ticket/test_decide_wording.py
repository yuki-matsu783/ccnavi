"""残った（未解決の）指摘を決めたあとに渡す文。0 件のときに、0 件の内訳や選び方を並べない。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from ccnavi.tickets import review, review_decide


def decision(unresolved=()):
    return SimpleNamespace(
        parent=SimpleNamespace(ticket="i0064"),
        ph=SimpleNamespace(number=1),
        unresolved=list(unresolved),
    )


class DecidedPromptTest(unittest.TestCase):
    def test_no_threads_says_reviewed_without_zero_counts(self):
        picked = {c: [] for c in review_decide.CHOICES}
        text = review_decide._decided_prompt("/w", decision(), picked, "")
        self.assertIn("フェーズ 1 をレビュー済みにした", text)
        self.assertIn("未解決（Unresolved）の指摘なし", text)
        self.assertNotIn("0 件", text)
        self.assertNotIn("対応方針を決めた", text)

    def test_only_chosen_destinations_are_counted(self):
        t = SimpleNamespace(url="u1", path="a.py", line=1, body="x")
        picked = {c: [] for c in review_decide.CHOICES}
        picked[review_decide.CHOICE_KEEP] = [t]
        text = review_decide._decided_prompt("/w", decision([t]), picked, "")
        self.assertIn("未解決（Unresolved）の指摘の対応方針を決めた（対応しない 1 件）", text)
        self.assertNotIn("0 件", text)


class ThreadLabelTest(unittest.TestCase):
    def label(self, **kw):
        base = dict(url="u1", path="", line=0, body="本文\n2 行目")
        return review.thread_label(SimpleNamespace(**{**base, **kw}))

    def test_thread_with_a_position_shows_file_and_line(self):
        self.assertEqual(self.label(path="a.py", line=3), "u1 a.py:3 本文")

    def test_thread_without_a_position_shows_no_file_or_zero(self):
        text = self.label()
        self.assertEqual(text, "u1 本文")
        self.assertNotIn(":0", text)

    def test_thread_with_a_file_but_no_line_shows_only_the_file(self):
        self.assertEqual(self.label(path="a.py"), "u1 a.py 本文")

    def test_decide_line_uses_the_same_label(self):
        t = SimpleNamespace(url="u1", path="", line=0, body="x")
        self.assertEqual(review_decide._thread_line(t), "u1 x")


if __name__ == "__main__":
    unittest.main()
