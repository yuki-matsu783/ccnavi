"""`ccnavi text <識別子> [名前=値 ...]` が、文面ファイルの文を出すか、
標準出力に何も出さずに失敗すること。

文面ファイルの読み込み・検査・組み立ては `go/internal/policy/texts` の go test が見る。
"""

from __future__ import annotations

import os
import tempfile
import unittest

from tests.inproc import run_ccnavi

COMMON = """\
version: 1
messages:
  NEXT_SAMPLE:
    kind: next
    values: [args_q]
    next: "打ち直す: {script:ccnavi-start.sh} {args_q}"
"""


class TextCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        path = os.path.join(self.root, ".ccnavi", "messages", "a.yml")
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(COMMON)

    def text(self, *words: str):
        return run_ccnavi(["--root", self.root, "text", *words])

    def test_the_cli_prints_the_text_or_fails_with_nothing_on_stdout(self):
        done = self.text("NEXT_SAMPLE", "args_q=--x")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(done.stdout.startswith("次: 打ち直す:"), done.stdout)
        done = self.text("NEXT_NOPE")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stdout, "")
        self.assertEqual(self.text().returncode, 2)


if __name__ == "__main__":
    unittest.main()
