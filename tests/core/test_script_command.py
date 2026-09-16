"""文面で案内する `.ccnavi/scripts/` の sh の綴り（settings.script_command）。

スクリプトはワークスペースにしか無いので、案内はワークスペースルートから綴る。
プロジェクトから切ったワークツリーでは、相対の `sh .ccnavi/scripts/...` が届かない。
"""

from __future__ import annotations

import os
import tempfile
import unittest

from ccnavi import phase, settings, shellread


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

    def test_quoted_only_when_needed(self):
        """空白を含むルートは引用する。引用しないと sh が単語に割る。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "My Projects", "ws")
            os.makedirs(root)
            real = os.path.realpath(root).replace("\\", "/")
            self.assertEqual(
                settings.script_command(root, "ccnavi-review.sh"),
                f'sh "{real}/.ccnavi/scripts/ccnavi-review.sh"',
            )
        # 二重引用符の中でも意味を持つ文字があれば、単引用符に落とす。
        spelled = settings.script_command("/tmp/a $b", "ccnavi-review.sh")
        self.assertTrue(spelled.startswith("sh '"), spelled)
        self.assertTrue(spelled.endswith("/.ccnavi/scripts/ccnavi-review.sh'"), spelled)

    def test_quoted_spelling_stays_exempt_and_forbidden(self):
        """空白を含むルートでも、案内どおりに打った形がゲートの例外と禁止に当たること。

        判定は hook と同じく shellread を通した文字列に当てる。引用の中の空白は
        区切りと別の目印になるので、`\\S*ccnavi-...` がパスを 1 語として読める。
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "My Projects", "ws")
            os.makedirs(root)
            review = settings.script_command(root, "ccnavi-review.sh")
            ticket = settings.script_command(root, "ccnavi-ticket.sh")
            request = shellread.read(f"{review} request --phase 1 --body-file b.md")
            self.assertTrue(phase.exempt(request.text, request.reason), request.text)
            ready = shellread.read(f"{review} ready")
            self.assertTrue(phase.forbidden(ready.text), ready.text)
            done = shellread.read(f"{ticket} done i0001-01")
            self.assertTrue(phase.forbidden(done.text), done.text)

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
