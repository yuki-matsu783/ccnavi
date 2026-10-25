"""読めなかった理由ごとの書き換え例と、非対話のときの 1 行（src/ccnavi/hook/reasons.py）。"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from ccnavi.hook import ccnavimd, reasons
from ccnavi.infra import shellread_scan, shellread_words

ANSWER_LINE = "確認に答えられない"
ASK_LINE = "確認に答えられません"


class RewriteHintTest(unittest.TestCase):
    def hint(self, reason, attended):
        env = {ccnavimd.ATTENDED_ENV: attended} if attended else {}
        with mock.patch.dict(os.environ, env, clear=False):
            if not attended:
                os.environ.pop(ccnavimd.ATTENDED_ENV, None)
            return "\n".join(reasons.rewrite_hint(reason))

    def test_理由ごとに書き換え例が出る(self):
        cases = {
            shellread_words.REASON_TAKEN_AS_CODE: "grep -rn",
            shellread_scan.REASON_ANSI_C: "grep -P",
            shellread_scan.REASON_UNTERMINATED: "Write ツール",
        }
        for reason, phrase in cases.items():
            with self.subTest(reason=reason):
                self.assertIn(phrase, self.hint(reason, ""))

    def test_非対話のときだけ書き直すほかないと言う(self):
        reason = shellread_words.REASON_TAKEN_AS_CODE
        self.assertIn(ANSWER_LINE, self.hint(reason, "0"))
        self.assertNotIn(ANSWER_LINE, self.hint(reason, "1"))
        self.assertNotIn(ANSWER_LINE, self.hint(reason, ""))

    def test_例の無い理由は何も足さない(self):
        self.assertEqual("", self.hint("no-such-reason", "0"))


class AskHintTest(unittest.TestCase):
    """確認（ask）の理由に足す説明です。非対話のときだけ足します。"""

    SHOWN = "sh x/ccnavi-worktree.sh a --base main"

    def hint(self, bare_id, attended):
        env = {ccnavimd.ATTENDED_ENV: attended} if attended else {}
        with mock.patch.dict(os.environ, env, clear=False):
            if not attended:
                os.environ.pop(ccnavimd.ATTENDED_ENV, None)
            return "\n".join(reasons.ask_hint(bare_id, self.SHOWN))

    def test_非対話のときだけ確認が要ることと次の行動を言う(self):
        said = self.hint("direct-worktree-confirm", "0")
        self.assertIn(ASK_LINE, said)
        self.assertIn(self.SHOWN, said)
        self.assertIn("了承", said)
        for attended in ("1", ""):
            with self.subTest(attended=attended):
                self.assertEqual("", self.hint("direct-worktree-confirm", attended))

    def test_adopt_はチケットに結び付く名前には通らないと言う(self):
        # 確認の理由が、あとのスクリプト側の拒否を隠さない（打ち直しても通らない案内にしない）。
        said = self.hint_for(
            "direct-worktree-confirm", "sh x/ccnavi-worktree.sh a --adopt --base main", "0"
        )

        self.assertIn("了承", said)
        self.assertIn("通りません", said)
        self.assertIn("チケットに結び付く", said)
        self.assertIn("マージリクエストで出します", said)
        self.assertIn("ccnavi-ticket.sh status", said)

    def test_adopt_の無い指定には_チケットの断りを足さない(self):
        for flags in ("--base main", "--base main --accept ab12", "--side-by-side"):
            with self.subTest(flags=flags):
                said = self.hint_for(
                    "direct-worktree-confirm", f"sh x/ccnavi-worktree.sh a {flags}", "0"
                )
                self.assertIn("了承", said)
                self.assertNotIn("通りません", said)

    def test_adopt_の断りも対話のときは出さない(self):
        for attended in ("1", ""):
            with self.subTest(attended=attended):
                self.assertEqual(
                    "",
                    self.hint_for(
                        "direct-worktree-confirm", "sh x/ccnavi-worktree.sh a --adopt", attended
                    ),
                )

    def test_adopt_は_dry_run_と語尾が違う語を取り違えない(self):
        said = self.hint_for(
            "direct-worktree-confirm", "sh x/ccnavi-worktree.sh a --adoption-x --base m", "0"
        )

        self.assertNotIn("通りません", said)

    def hint_for(self, bare_id, shown, attended):
        env = {ccnavimd.ATTENDED_ENV: attended} if attended else {}
        with mock.patch.dict(os.environ, env, clear=False):
            if not attended:
                os.environ.pop(ccnavimd.ATTENDED_ENV, None)
            return "\n".join(reasons.ask_hint(bare_id, shown))

    def test_prefer_webfetch_は代わりの手も言う(self):
        self.assertIn("WebFetch", self.hint("prefer-webfetch", "0"))
        self.assertNotIn("WebFetch", self.hint("dependencies", "0"))
        self.assertEqual("", self.hint("prefer-webfetch", ""))


if __name__ == "__main__":
    unittest.main()
