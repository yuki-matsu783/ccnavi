"""`--lint` が `.ccnavi/CCNAVI.md` の条件と差し込みの誤りを言うことの受入テスト。

条件と差し込みの解き方そのものは `go/internal/hook/ccnavimd` の go test が見る。
"""

from __future__ import annotations

import os
import tempfile
import unittest

from tests import ROOT
from tests.inproc import run_ccnavi


class LintTest(unittest.TestCase):
    def lint(self, text: str | None) -> str:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = os.path.realpath(directory.name)
        os.makedirs(os.path.join(root, ".ccnavi"))
        if text is not None:
            with open(os.path.join(root, ".ccnavi", "CCNAVI.md"), "w", encoding="utf-8") as f:
                f.write(text)
        return run_ccnavi(["--lint", "--root", root], cwd=ROOT).stdout

    def test_無ければwarnで言う(self):
        self.assertIn("(ccnavi-md)", self.lint(None))

    def test_誤りは行番号つきでwarnに出る(self):
        out = self.lint("<!-- ccnavi:if nothing -->\n")

        self.assertIn("(ccnavi-md)", out)
        self.assertIn("1 行目: 条件 nothing は指定できない", out)

    def test_名前に_以外の記号があればerrorで言う(self):
        out = self.lint("<!-- ccnavi:if has-docker -->\n<!-- ccnavi:endif -->\n")

        self.assertIn("(ccnavi-md)", out)
        self.assertIn("条件の名前 has-docker は指定できない", out)
        self.assertIn("error", out.lower())

    def test_正しければ言わない(self):
        self.assertNotIn("(ccnavi-md)", self.lint("# タイトル\n"))

    def test_ccnavi_ディレクトリが無いワークスペースは言わない(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = os.path.realpath(directory.name)
        out = run_ccnavi(["--lint", "--root", root], cwd=ROOT, messages=False).stdout

        self.assertNotIn("(ccnavi-md)", out)


if __name__ == "__main__":
    unittest.main()
