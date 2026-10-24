"""`.ccnavi/CCNAVI.md` の条件と差し込みを解く部品の単体テスト。"""

from __future__ import annotations

import os
import tempfile
import types
import unittest
from unittest import mock

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
        text = (
            "<!-- ccnavi:if a -->\n<!-- ccnavi:if b -->\nx\n<!-- ccnavi:else -->\ny\n"
            "<!-- ccnavi:endif -->\n<!-- ccnavi:endif -->"
        )

        self.assertEqual(ccnavimd.render(text, {"b"}, {}), "")
        self.assertEqual(ccnavimd.render(text, set(), {}), "")

    def test_elseの誤りを行番号つきで挙げる(self):
        self.assertEqual(
            ccnavimd.problems(ELSE_TEXT.replace("a", "docs").replace("b", "tickets")), []
        )
        joined = "\n".join(
            ccnavimd.problems(
                "<!-- ccnavi:else -->\n<!-- ccnavi:if docs -->\n<!-- ccnavi:else -->\n"
                "<!-- ccnavi:else -->\n<!-- ccnavi:endif -->\n"
            )
        )
        self.assertIn("1 行目: ccnavi:else に対応する ccnavi:if がありません。", joined)
        self.assertIn("4 行目: 1 つの ccnavi:if に ccnavi:else は 1 つだけ", joined)


class IndentTest(unittest.TestCase):
    def test_囲みの深さの分だけインデントを取る(self):
        text = (
            "<!-- ccnavi:if a -->\n  - a\n    - 深い\n  <!-- ccnavi:if b -->\n    - b\n"
            "  <!-- ccnavi:endif -->\n"
            "<!-- ccnavi:endif -->\n- 外"
        )

        self.assertEqual(ccnavimd.render(text, {"a", "b"}, {}), "- a\n  - 深い\n- b\n- 外")

    def test_インデントの無い囲みもそのまま載る(self):
        self.assertEqual(
            ccnavimd.render("<!-- ccnavi:if a -->\n- a\n<!-- ccnavi:endif -->", {"a"}, {}), "- a"
        )


class ScriptConditionTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = os.path.realpath(directory.name)
        self.conf = types.SimpleNamespace(project_home=None)
        self.script = os.path.join(self.root, ".ccnavi", "scripts", "ccnavimd.sh")
        os.makedirs(os.path.dirname(self.script))

    def write(self, body: str):
        with open(self.script, "w", encoding="utf-8") as f:
            f.write(body)

    def flags(self, md: str, **kw) -> set[str]:
        ctx = ccnavimd.Context(
            self.conf,  # type: ignore[arg-type]
            self.root,
            kw.get("docs", False),
            kw.get("deny", set()),
            "enable",
        )
        return ccnavimd.script_flags(self.conf, self.root, md, ctx)  # type: ignore[arg-type]

    def uses(self, *names: str) -> str:
        return "".join(f"<!-- ccnavi:if {n} -->\n<!-- ccnavi:endif -->\n" for n in names)

    def test_1つのshに書いた複数の関数がそれぞれ判定する(self):
        self.write(
            "ccnavi_cond_yes() { return 0; }\n"
            "ccnavi_cond_no() { return 1; }\n"
            "ccnavi_cond_has_docker() { return 0; }\n"
            "ccnavi_cond_broken() { return 127; }\n"
        )

        # 0 が真、1 が偽、それ以外は判定できないので真
        self.assertEqual(
            self.flags(self.uses("yes", "no", "has_docker", "broken")),
            {"yes", "has_docker", "broken"},
        )

    def test_関数もshも無い名前は判定できないので真(self):
        self.assertEqual(self.flags(self.uses("nothing")), {"nothing"})
        self.write("ccnavi_cond_other() { return 1; }\n")
        self.assertEqual(self.flags(self.uses("nothing")), {"nothing"})

    def test_shが壊れていても真(self):
        self.write("ccnavi_cond_x() { \n")

        self.assertEqual(self.flags(self.uses("x")), {"x"})

    def test_状況は環境変数で渡る(self):
        self.write(
            "ccnavi_cond_env() {\n"
            '  [ "$CCNAVI_DOCS" = 1 ] || return 1\n'
            '  case " $CCNAVI_DENY_IDS " in *" a "*) return 0;; esac\n'
            "  return 1\n"
            "}\n"
        )

        self.assertEqual(self.flags(self.uses("env"), docs=True, deny={"a", "b"}), {"env"})
        self.assertEqual(self.flags(self.uses("env"), docs=False, deny={"a"}), set())

    def test_組み込みと同じ名前の関数は無視し_使っていない関数は走らせない(self):
        marker = os.path.join(self.root, "ran")
        self.write(
            f"ccnavi_cond_docs() {{ return 1; }}\nccnavi_cond_unused() {{ touch '{marker}'; }}\n"
        )

        self.assertEqual(self.flags(self.uses("docs")), set())
        self.assertFalse(os.path.exists(marker))
        self.assertEqual(ccnavimd.script_names(self.conf, self.root), {"unused"})  # type: ignore[arg-type]

    def test_lintは関数のある名前を許す(self):
        self.write("ccnavi_cond_has_docker() { return 0; }\n")
        text = self.uses("has_docker")

        self.assertEqual(
            ccnavimd.problems(text, ccnavimd.script_names(self.conf, self.root)),  # type: ignore[arg-type]
            [],
        )
        self.assertIn("条件 has_docker は指定できない", "\n".join(ccnavimd.problems(text)))


class NameTest(unittest.TestCase):
    def test_名前に使えない形は行番号つきで挙げる(self):
        text = (
            "<!-- ccnavi:if has-docker -->\n<!-- ccnavi:endif -->\n"
            "<!-- ccnavi:if 1a -->\n<!-- ccnavi:endif -->\n"
        )

        found = ccnavimd.name_problems(text)

        self.assertEqual(len(found), 2)
        self.assertIn("1 行目: 条件の名前 has-docker は指定できない", found[0])
        self.assertIn("3 行目: 条件の名前 1a は指定できない", found[1])
        self.assertEqual(ccnavimd.problems(text), [])  # 名前の形は name_problems が言う

    def test_組み込みの名前はすべて使える形(self):
        self.assertTrue(all(ccnavimd.NAME.match(n) for n in ccnavimd.CONDITIONS))


class FlagsTest(unittest.TestCase):
    def test_条件は関数の真偽で決まる(self):
        ctx = ccnavimd.Context(None, "", True, {"workspace-root"}, "dry-run")  # type: ignore[arg-type]

        self.assertTrue(ccnavimd.CONDITIONS["workspace_root"](ctx))
        self.assertTrue(ccnavimd.CONDITIONS["docs"](ctx))
        self.assertTrue(ccnavimd.CONDITIONS["dry_run"](ctx))

    def test_非対話は付き添いなしと示されたときだけ真(self):
        ctx = ccnavimd.Context(None, "", False, set(), "enable")  # type: ignore[arg-type]
        check = ccnavimd.CONDITIONS["non_interactive"]

        with mock.patch.dict(os.environ, {ccnavimd.ATTENDED_ENV: "0"}):
            self.assertTrue(check(ctx))
        for value in ("1", "", "true", "0 "):
            with (
                self.subTest(value=value),
                mock.patch.dict(os.environ, {ccnavimd.ATTENDED_ENV: value}),
            ):
                self.assertFalse(check(ctx))
        with mock.patch.dict(os.environ):
            os.environ.pop(ccnavimd.ATTENDED_ENV, None)
            self.assertFalse(check(ctx))


class ProblemsTest(unittest.TestCase):
    def test_誤りを行番号つきで挙げる(self):
        found = ccnavimd.problems(
            "<!-- ccnavi:if nothing -->\n{{nope}}\n<!-- ccnavi:endif -->\n<!-- ccnavi:endif -->\n"
            "<!-- ccnavi:if docs -->\n"
        )

        joined = "\n".join(found)
        self.assertIn("1 行目: 条件 nothing は指定できない", joined)
        self.assertIn("2 行目: 差し込み {{nope}} は指定できない", joined)
        self.assertIn("4 行目: ccnavi:endif に対応する ccnavi:if がありません。", joined)
        self.assertIn("ccnavi:if に対応する ccnavi:endif がありません。", joined)

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
        self.assertIn("1 行目: 条件 nothing は指定できない", out)

    def test_名前に_以外の記号があればerrorで言う(self):
        out = self.lint("<!-- ccnavi:if has-docker -->\n<!-- ccnavi:endif -->\n")

        self.assertIn("(ccnavi-md)", out)
        self.assertIn("条件の名前 has-docker は指定できない", out)
        self.assertIn("error", out.lower())

    def test_正しければ言わない(self):
        self.assertNotIn("(ccnavi-md)", self.lint("# 題\n"))

    def test_ccnavi_ディレクトリが無いワークスペースは言わない(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        out = run_ccnavi(["--lint", "--root", os.path.realpath(directory.name)], cwd=ROOT).stdout

        self.assertNotIn("(ccnavi-md)", out)


if __name__ == "__main__":
    unittest.main()
