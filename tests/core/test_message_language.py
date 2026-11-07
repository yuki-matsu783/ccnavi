"""ユーザとエージェントに届く文面が、日本語のまま書かれていることの検出（#561）。

ASCII だけの文は、英語のまま出ている。コード・識別子・コマンド・パスは英数字だけで書かれるので、
文として読める長さ（英単語 4 語以上、かつ空白で区切った語に小文字の英単語が混じる）で拾う。
内部エラーの文は `go/internal/hook/reasons` の go test が文ごと見る。
"""

from __future__ import annotations

import os
import re
import tempfile
import unittest

from tests.inproc import run_ccnavi

ENGLISH_SENTENCE = re.compile(r"\b[A-Za-z]{2,}(?:\s+[A-Za-z']{2,}){3,}")


def english_in(text: str) -> list[str]:
    """日本語を含まない行のうち、英語の文に見えるもの。"""
    return [
        line
        for line in text.splitlines()
        if ENGLISH_SENTENCE.search(line) and not re.search(r"[぀-ヿ一-鿿]", line)
    ]


def response(root: str, command: str) -> str:
    """`ccnavi --test Bash <command>` が返す文（`response:` より後）。"""
    done = run_ccnavi(["--root", root, "--mode", "enable", "--test", "Bash", command])
    return done.stdout.partition("\nresponse:\n")[2]


class MessageLanguageTest(unittest.TestCase):
    def test_the_detector_finds_an_english_sentence(self):
        self.assertTrue(english_in("note: this is the approved area."))
        self.assertFalse(english_in("案内: 承認済みの置き場です。"))

    def test_refusal_texts_keep_their_hints(self):
        with tempfile.TemporaryDirectory() as root:
            deletes = response(root, "rm -rf x")
            self.assertIn("rmdir", deletes)
            self.assertIn("scratchpad/", deletes)
            self.assertEqual(english_in(deletes), [])
            os.makedirs(os.path.join(root, ".ccnavi", "scripts"))
            with open(os.path.join(root, ".ccnavi", "scripts", "ccnavi-git.sh"), "w") as f:
                f.write("")
            said = response(root, "git log")
        self.assertIn("status・log・diff・show・blame", said)
        self.assertIn("log -5", said)
        self.assertEqual(english_in(said), [])


if __name__ == "__main__":
    unittest.main()
