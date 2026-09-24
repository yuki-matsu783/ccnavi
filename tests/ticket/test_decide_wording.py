"""残った（未解決の）指摘を決めたあとに渡す文。0 件のときに、0 件の内訳や選び方を並べない。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from ccnavi import review


def decision(unresolved=()):
    return SimpleNamespace(
        parent=SimpleNamespace(ticket="i0064"),
        ph=SimpleNamespace(number=1),
        unresolved=list(unresolved),
    )


class DecidedPromptTest(unittest.TestCase):
    def test_no_threads_says_reviewed_without_zero_counts(self):
        picked = {c: [] for c in review.CHOICES}
        text = review._decided_prompt("/w", decision(), picked, "")
        self.assertIn("フェーズ 1 をレビュー済みにした", text)
        self.assertIn("未解決（Unresolved）の指摘なし", text)
        self.assertNotIn("0 件", text)
        self.assertNotIn("行き先を決めた", text)

    def test_only_chosen_destinations_are_counted(self):
        t = SimpleNamespace(url="u1", path="a.py", line=1, body="x")
        picked = {c: [] for c in review.CHOICES}
        picked[review.CHOICE_KEEP] = [t]
        text = review._decided_prompt("/w", decision([t]), picked, "")
        self.assertIn("未解決（Unresolved）の指摘の行き先を決めた（対応しない 1 件）", text)
        self.assertNotIn("0 件", text)


if __name__ == "__main__":
    unittest.main()
