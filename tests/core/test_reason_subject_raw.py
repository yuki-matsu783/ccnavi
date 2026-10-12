"""拒否文の `subject:` は、読み直す前のコマンドの原文を出す（引用符と空白を落とさない）。
秘密の形は記録と同じく伏せる。"""

from __future__ import annotations

import unittest

from tests.core import test_additional_context as base


class ReasonSubjectRawTest(unittest.TestCase):
    def setUp(self):
        self.case = base.AdditionalContextTest("test_allow_carries_the_text_as_additional_context")
        self.case.setUp()
        self.addCleanup(self.case.dir.cleanup)

    def reason(self, command: str) -> str:
        self.case.rules(deny=[base.rule("redir", "Bash", glob="*>> a.yml*")])
        out = self.case.judge("Bash", command)
        self.assertEqual(out.get("permissionDecision"), "deny")
        return out.get("permissionDecisionReason", "")

    def test_拒否文のsubjectは引用符と空白を保つ(self):
        command = 'echo "# x" >> a.yml'
        self.assertIn(f"subject: {command}", self.reason(command))

    def test_拒否文のsubjectは秘密の形を伏せる(self):
        secret = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
        text = self.reason(f"echo {secret} >> a.yml")
        self.assertNotIn(secret, text)


if __name__ == "__main__":
    unittest.main()
