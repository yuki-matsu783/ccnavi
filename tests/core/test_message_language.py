"""ユーザとエージェントに届く文面が、日本語のまま書かれていることの検出（#561）。

ASCII だけの文は、英語のまま出ている。コード・識別子・コマンド・パスは英数字だけで書かれるので、
文として読める長さ（英単語 4 語以上、かつ空白で区切った語に小文字の英単語が混じる）で拾う。
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import unittest

from ccnavi.hook import post_report, reasons
from ccnavi.policy import texts
from tests import ROOT

ENGLISH_SENTENCE = re.compile(r"\b[A-Za-z]{2,}(?:\s+[A-Za-z']{2,}){3,}")


def english_in(text: str) -> list[str]:
    """日本語を含まない行のうち、英語の文に見えるもの。"""
    return [
        line
        for line in text.splitlines()
        if ENGLISH_SENTENCE.search(line) and not re.search(r"[぀-ヿ一-鿿]", line)
    ]


class MessageLanguageTest(unittest.TestCase):
    def test_the_detector_finds_an_english_sentence(self):
        self.assertTrue(english_in("note: this is the approved area."))
        self.assertFalse(english_in("案内: 承認済みの置き場です。"))

    def test_internal_error_is_in_japanese(self):
        from ccnavi.infra import hookio

        for event in (hookio.PRE_TOOL_USE, hookio.POST_TOOL_USE):
            for passed in (False, True):
                text = reasons.internal_error(ROOT, "X", event, "IndexError", "/log", passed=passed)
                self.assertEqual(english_in(text), [], text)
        self.assertEqual(english_in(reasons.internal_error_notice("X", "Stop", "E", "/log")), [])

    def test_approved_hint_is_in_japanese(self):
        hint = texts.say(ROOT, "NOTE_POST_APPROVED_AREA")
        self.assertEqual(english_in(hint), [])
        self.assertIn("承認済み", hint)

    def test_refusal_texts_keep_their_hints(self):
        from ccnavi.policy import builtin
        from ccnavi.tickets import ticket_guard

        with tempfile.TemporaryDirectory() as plain:
            shutil.copytree(
                os.path.join(ROOT, ".ccnavi", "messages"),
                os.path.join(plain, ".ccnavi", "messages"),
            )
            deletes = {r.id: texts.spoken(r) for r in builtin.load_base(plain)[0].deny}
        self.assertIn("rmdir", deletes["builtin:recursive-delete"])
        self.assertIn("scratchpad/", deletes["builtin:recursive-delete"])
        self.assertEqual(english_in(deletes["builtin:recursive-delete"]), [])
        with tempfile.TemporaryDirectory() as root:
            shutil.copytree(
                os.path.join(ROOT, ".ccnavi", "messages"),
                os.path.join(root, ".ccnavi", "messages"),
            )
            os.makedirs(os.path.join(root, ".ccnavi", "scripts"))
            with open(os.path.join(root, ".ccnavi", "scripts", "ccnavi-git.sh"), "w") as f:
                f.write("")
            said = texts.spoken(ticket_guard.raw_git_rule(root))
        self.assertIn("status・log・diff・show・blame", said)
        self.assertIn("log -5", said)


if __name__ == "__main__":
    unittest.main()
