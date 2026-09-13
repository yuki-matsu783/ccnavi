"""文面で案内する `.ccnavi/scripts/` の sh の綴り（settings.script_command）。

スクリプトはワークスペースにしか無いので、案内はワークスペースルートから綴る。
プロジェクトから切った作業ツリーでは、相対の `sh .ccnavi/scripts/...` が届かない。
"""

from __future__ import annotations

import os
import tempfile
import unittest

from ccnavi import phase, settings


class ScriptCommandTest(unittest.TestCase):
    def test_spelled_from_workspace_root(self):
        with tempfile.TemporaryDirectory() as root:
            real = os.path.realpath(root).replace("\\", "/")
            self.assertEqual(
                settings.script_command(root, "ccnavi-review.sh"),
                f"sh {real}/.ccnavi/scripts/ccnavi-review.sh",
            )
            # 末尾の区切りは重ねない。
            self.assertEqual(
                settings.script_command(root + os.sep, "ccnavi-git.sh"),
                f"sh {real}/.ccnavi/scripts/ccnavi-git.sh",
            )

    def test_separator_is_slash(self):
        """Windows の `\\` は `/` に寄せる。Git Bash は `C:/...` を読める。"""
        spelled = settings.script_command("C:\\Users\\me\\ws", "ccnavi-review.sh")
        self.assertNotIn("\\", spelled)
        self.assertTrue(spelled.endswith("/ws/.ccnavi/scripts/ccnavi-review.sh"), spelled)

    def test_spelling_stays_exempt_and_forbidden(self):
        """案内どおりに打った形が、ゲートの例外にもサブエージェントの禁止にも当たること。

        案内だけ絶対パスにして、判定が相対の綴りしか見ていなければ、案内どおり打った
        レビューの依頼がゲートに止められる。
        """
        with tempfile.TemporaryDirectory() as root:
            review = settings.script_command(root, "ccnavi-review.sh")
            ticket = settings.script_command(root, "ccnavi-ticket.sh")
            self.assertTrue(phase.exempt(f"{review} request --phase 1 --body-file b.md", ""))
            self.assertTrue(phase.exempt(f"{review} check --phase 1", ""))
            self.assertTrue(phase.forbidden(f"{review} ready"))
            self.assertTrue(phase.forbidden(f"{ticket} done i0001-01"))
            rule = phase.ticket_approval_rule("", root)
            approve = settings.script_command(root, "ccnavi-approve.sh")
            self.assertIsNotNone(rule.compiled.search(approve))
            self.assertIn(approve, rule.message)
            self.assertNotIn("sh .ccnavi/scripts/", rule.message)


if __name__ == "__main__":
    unittest.main()
