"""共通の phases.yml の `quick-work` が `review: true` であることの受入テスト（#379）。"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

PHASES = Path(__file__).resolve().parents[2] / ".ccnavi" / "config" / "common" / "phases.yml"


class CommonQuickReviewTest(unittest.TestCase):
    def test_quick_reviews_by_mr(self):
        text = PHASES.read_text(encoding="utf-8")
        m = re.search(r"^  quick-work:\n((?:    .*\n)+)", text, re.MULTILINE)
        self.assertIsNotNone(m, "quick-work の定義が無い")
        self.assertRegex(m.group(1), r"(?m)^    review: true$")


if __name__ == "__main__":
    unittest.main()
