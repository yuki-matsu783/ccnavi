"""読めなかった理由ごとの書き換え例と、非対話のときの 1 行（src/ccnavi/hook/reasons.py）。"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from ccnavi.hook import ccnavimd, reasons
from ccnavi.infra import shellread_scan, shellread_words

ANSWER_LINE = "確認に答えられない"


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


if __name__ == "__main__":
    unittest.main()


class AskHintTest(unittest.TestCase):
    """確認（ask）の理由に足す、非対話のときだけの説明。"""

    SHOWN = "sh x/ccnavi-worktree.sh a --base main"

    def hint(self, bare_id, attended):
        env = {ccnavimd.ATTENDED_ENV: attended} if attended else {}
        with mock.patch.dict(os.environ, env, clear=False):
            if not attended:
                os.environ.pop(ccnavimd.ATTENDED_ENV, None)
            return "\n".join(reasons.ask_hint(bare_id, self.SHOWN))

    def test_非対話のときだけ確認が要ることと次の行動を言う(self):
        said = self.hint("direct-worktree-confirm", "0")
        self.assertIn(ANSWER_LINE, said)
        self.assertIn(self.SHOWN, said)
        self.assertIn("了承", said)
        for attended in ("1", ""):
            with self.subTest(attended=attended):
                self.assertEqual("", self.hint("direct-worktree-confirm", attended))

    def test_prefer_webfetch_は代わりの手も言う(self):
        self.assertIn("WebFetch", self.hint("prefer-webfetch", "0"))
        self.assertNotIn("WebFetch", self.hint("dependencies", "0"))
        self.assertEqual("", self.hint("prefer-webfetch", ""))
