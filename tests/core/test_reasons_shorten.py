"""理由の文面に載せる対象の切り詰め（reasons._shorten）のテスト。

どの文面でも「畳む → 切る → 畳んだ後の長さで残りを数える」の同じ形で切る。
文面ごとに手で書くと、目印を付け忘れたり、畳む前の長さで数えたりして揃わない。
"""

from __future__ import annotations

import unittest

from ccnavi import reasons

LIMIT = reasons.SUBJECT_LIMIT


def subject_line(text: str) -> str:
    return next(line for line in text.splitlines() if line.startswith("subject: "))


class ShortenTest(unittest.TestCase):
    def test_上限以内なら畳むだけで目印を付けない(self):
        self.assertEqual(reasons._shorten("git\n  push   origin"), "git push origin")

    def test_残りの字数は畳んだ後の長さで数える(self):
        # 改行と空白の続きを残りに入れると、見えていない字数を多く言う。
        text = "a\n\n\n   " * LIMIT
        folded = " ".join(text.split())
        shown = reasons._shorten(text)
        self.assertEqual(shown, folded[:LIMIT] + f"…(+{len(folded) - LIMIT})")

    def test_サブエージェントの文面も切った目印を付ける(self):
        text = reasons.subagent_forbidden("x" * (LIMIT + 5), "", "")
        self.assertEqual(subject_line(text), "subject: " + "x" * LIMIT + "…(+5)")

    def test_組み込みの判定の文面も切った目印を付ける(self):
        text = reasons.builtin_refusal("CODE", "y" * (LIMIT + 7), "text")
        self.assertEqual(subject_line(text), "subject: " + "y" * LIMIT + "…(+7)")

    def test_宣言の無い呼び出しの文面も畳んだ後の長さで数える(self):
        subject = "z\n" * (LIMIT + 3)
        text = reasons.undeclared("Bash", subject, "rules.yml", "", False)
        folded = " ".join(subject.split())
        self.assertEqual(
            subject_line(text), "subject: " + folded[:LIMIT] + f"…(+{len(folded) - LIMIT})"
        )


if __name__ == "__main__":
    unittest.main()
