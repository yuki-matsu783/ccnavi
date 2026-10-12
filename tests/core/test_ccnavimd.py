"""`.ccnavi/CCNAVI.md` の条件と差し込みを解く部品の単体テスト。"""

from __future__ import annotations

import os
import tempfile
import unittest

from ccnavi.hook import ccnavimd
from tests import ROOT
from tests.inproc import run_ccnavi

TEXT = """# 題
- いつも
<!-- ccnavi:if a -->
- a のとき {{x}}
<!-- ccnavi:if b -->
- a と b のとき
<!-- ccnavi:endif -->
<!-- ccnavi:endif -->

<!-- ccnavi:if b -->
- b のとき
<!-- ccnavi:endif -->
- 最後
"""


class RenderTest(unittest.TestCase):
    def test_成り立つ条件の行だけが残る(self):
        self.assertEqual(ccnavimd.render(TEXT, set(), {}), "# 題\n- いつも\n\n- 最後")
        self.assertEqual(ccnavimd.render(TEXT, {"b"}, {}), "# 題\n- いつも\n\n- b のとき\n- 最後")

    def test_入れ子は外も内も成り立つときだけ載る(self):
        out = ccnavimd.render(TEXT, {"a", "b"}, {"x": "値"})

        self.assertIn("- a のとき 値", out)
        self.assertIn("- a と b のとき", out)
        self.assertNotIn("- a と b のとき", ccnavimd.render(TEXT, {"a"}, {}))

    def test_知らない差し込みはそのまま残る(self):
        self.assertEqual(ccnavimd.render("{{nothing}}", set(), {}), "{{nothing}}")

    def test_空行は続けて1つにまとめ前後は取る(self):
        self.assertEqual(ccnavimd.render("\n\na\n\n\n\nb\n\n", set(), {}), "a\n\nb")


ELSE_TEXT = """<!-- ccnavi:if a -->
- a
<!-- ccnavi:if b -->
- a と b
<!-- ccnavi:else -->
- a で b でない
<!-- ccnavi:endif -->
<!-- ccnavi:else -->
- a でない
<!-- ccnavi:endif -->
"""


class ElseTest(unittest.TestCase):
    def test_成り立たないときの行が載る(self):
        self.assertEqual(ccnavimd.render(ELSE_TEXT, set(), {}), "- a でない")
        self.assertEqual(ccnavimd.render(ELSE_TEXT, {"a"}, {}), "- a\n- a で b でない")
        self.assertEqual(ccnavimd.render(ELSE_TEXT, {"a", "b"}, {}), "- a\n- a と b")

    def test_外側が成り立たなければ内側のelseも載らない(self):
        text = "<!-- ccnavi:if a -->\n<!-- ccnavi:if b -->\nx\n<!-- ccnavi:else -->\ny\n<!-- ccnavi:endif -->\n<!-- ccnavi:endif -->"

        self.assertEqual(ccnavimd.render(text, {"b"}, {}), "")
        self.assertEqual(ccnavimd.render(text, set(), {}), "")

    def test_elseの誤りを行番号つきで挙げる(self):
        self.assertEqual(ccnavimd.problems(ELSE_TEXT.replace("a", "docs").replace("b", "tickets")), [])
        joined = "\n".join(
            ccnavimd.problems(
                "<!-- ccnavi:else -->\n<!-- ccnavi:if docs -->\n<!-- ccnavi:else -->\n"
                "<!-- ccnavi:else -->\n<!-- ccnavi:endif -->\n"
            )
        )
        self.assertIn("1 行目: ccnavi:else に対応する ccnavi:if が無い", joined)
        self.assertIn("4 行目: 1 つの ccnavi:if に ccnavi:else は 1 つだけ", joined)


class IndentTest(unittest.TestCase):
    def test_囲みの深さの分だけインデントを取る(self):
        text = (
            "<!-- ccnavi:if a -->\n  - a\n    - 深い\n  <!-- ccnavi:if b -->\n    - b\n  <!-- ccnavi:endif -->\n"
            "<!-- ccnavi:endif -->\n- 外"
        )

        self.assertEqual(ccnavimd.render(text, {"a", "b"}, {}), "- a\n  - 深い\n- b\n- 外")

    def test_インデントの無い囲みもそのまま載る(self):
        self.assertEqual(ccnavimd.render("<!-- ccnavi:if a -->\n- a\n<!-- ccnavi:endif -->", {"a"}, {}), "- a")


class FlagsTest(unittest.TestCase):
    def test_条件は関数の真偽で決まる(self):
        ctx = ccnavimd.Context(None, "", True, {"workspace-root"}, "dry-run")  # type: ignore[arg-type]

        self.assertTrue(ccnavimd.CONDITIONS["workspace-root"](ctx))
        self.assertTrue(ccnavimd.CONDITIONS["docs"](ctx))
        self.assertTrue(ccnavimd.CONDITIONS["dry-run"](ctx))


class ProblemsTest(unittest.TestCase):
    def test_誤りを行番号つきで挙げる(self):
        found = ccnavimd.problems(
            "<!-- ccnavi:if nothing -->\n{{nope}}\n<!-- ccnavi:endif -->\n<!-- ccnavi:endif -->\n"
            "<!-- ccnavi:if docs -->\n"
        )

        joined = "\n".join(found)
        self.assertIn("1 行目: 条件 nothing は使えない", joined)
        self.assertIn("2 行目: 差し込み {{nope}} は使えない", joined)
        self.assertIn("4 行目: ccnavi:endif に対応する ccnavi:if が無い", joined)
        self.assertIn("ccnavi:if に対応する ccnavi:endif が無い", joined)

    def test_このリポジトリのccnavi_mdに誤りは無い(self):
        with open(os.path.join(ROOT, ".ccnavi", "CCNAVI.md"), encoding="utf-8") as f:
            self.assertEqual(ccnavimd.problems(f.read()), [])


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
        self.assertIn("1 行目: 条件 nothing は使えない", out)

    def test_正しければ言わない(self):
        self.assertNotIn("(ccnavi-md)", self.lint("# 題\n"))

    def test_ccnavi_ディレクトリが無いワークスペースは言わない(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        out = run_ccnavi(["--lint", "--root", os.path.realpath(directory.name)], cwd=ROOT).stdout

        self.assertNotIn("(ccnavi-md)", out)


if __name__ == "__main__":
    unittest.main()
