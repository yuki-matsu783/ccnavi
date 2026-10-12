"""拒否文の `subject:` は、読み直す前のコマンドの原文を出す（引用符と空白を落とさない）。"""

from __future__ import annotations

import unittest

from tests.core.test_additional_context import AdditionalContextTest, rule


class ReasonSubjectRawTest(AdditionalContextTest):
    def test_拒否文のsubjectは引用符と空白を保つ(self):
        self.rules(deny=[rule("redir", "Bash", glob="*>> a.yml*")])
        command = 'echo "# x" >> a.yml'
        out = self.judge("Bash", command)
        self.assertEqual(out.get("permissionDecision"), "deny")
        self.assertIn(f"subject: {command}", out.get("permissionDecisionReason", ""))


# 親クラスのテストを再実行しない。
del AdditionalContextTest

if __name__ == "__main__":
    unittest.main()
