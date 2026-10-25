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
