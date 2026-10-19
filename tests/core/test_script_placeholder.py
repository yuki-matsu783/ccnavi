"""ルールの文の差し込み `{script:<名前>}`、案内のコマンドの引用、直接作業の案内の文面の単体テスト。

保護対象（rules.yml・CCNAVI.md）は、下書きがあれば下書きの、無ければ本物の「できあがりの姿」を読む
（tests/draft.py）。
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import unittest

import yaml

from ccnavi.hook import ccnavimd
from ccnavi.infra import settings
from ccnavi.policy import rule_lint, rules
from ccnavi.tickets import ticket_ids
from tests import ROOT
from tests.draft import final
from tests.inproc import run_ccnavi


class FillTextTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = os.path.realpath(self.dir.name)

    def test_script_is_replaced_by_the_script_command(self):
        got = rules.fill_text("a {script:ccnavi-worktree.sh} <名前> b", self.root)
        self.assertEqual(
            got, f"a {settings.script_command(self.root, 'ccnavi-worktree.sh')} <名前> b"
        )

    def test_root_is_still_a_bare_path(self):
        self.assertEqual(
            rules.fill_text("{root}/x", self.root), rules.fill_root("{root}/x", self.root)
        )

    def test_a_bad_name_is_left_as_written(self):
        for text in ("{script:a b.sh}", "{script:}", "{script:../x}", "{script:a/b}"):
            with self.subTest(text=text):
                self.assertEqual(rules.fill_text(text, self.root), text)

    def test_a_missing_script_is_still_replaced(self):
        got = rules.fill_text("{script:nothing.sh}", self.root)
        self.assertEqual(got, settings.script_command(self.root, "nothing.sh"))

    def test_without_a_root_nothing_is_replaced(self):
        self.assertEqual(rules.fill_text("{script:a.sh} {root}", ""), "{script:a.sh} {root}")

    def test_a_path_with_space_and_quote_is_quoted_and_runs_from_a_fresh_shell(self):
        base = os.path.join(self.root, "a b's")
        os.makedirs(os.path.join(base, ".ccnavi", "scripts"))
        with open(os.path.join(base, ".ccnavi", "scripts", "x.sh"), "w") as f:
            f.write("echo ran\n")
        import subprocess

        got = rules.fill_text("{script:x.sh}", base)
        done = subprocess.run(["sh", "-c", got], cwd="/", capture_output=True, text=True)
        self.assertEqual((done.returncode, done.stdout.strip()), (0, "ran"), done.stderr)

    def test_shell_path_quotes_like_script_command(self):
        self.assertEqual(settings.shell_path("/a/b"), "/a/b")
        self.assertEqual(settings.shell_path("/a b/c"), '"/a b/c"')
        self.assertEqual(settings.shell_path("C:\\a b"), '"C:/a b"')
        # POSIX の `\` は名前の一部で、`/` に変えない。
        self.assertEqual(settings.shell_path("/a\\b"), "'/a\\b'")
        self.assertEqual(settings.shell_path('/a"b'), "'/a\"b'")


class LintTest(unittest.TestCase):
    def lint(self, message: str, scripts: tuple[str, ...] = ()) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.realpath(tmp)
            for name in scripts:
                os.makedirs(os.path.join(root, ".ccnavi", "scripts"), exist_ok=True)
                open(os.path.join(root, ".ccnavi", "scripts", name), "w").close()
            path = os.path.join(root, "rules.yml")
            with open(path, "w", encoding="utf-8") as f:
                yaml.safe_dump(
                    {
                        "version": 1,
                        "allow": [
                            {
                                "id": "r",
                                "match": "Bash",
                                "regex": "^ccnavi-only-this$",
                                "additionalContext": message,
                            }
                        ],
                    },
                    f,
                    allow_unicode=True,
                )
            problems = rule_lint.file_problems(path, root)
        return [p.detail for p in problems if p.severity == rules.SEVERITY_WARN]

    def test_a_good_placeholder_with_the_script_present_is_quiet(self):
        self.assertEqual(self.lint("{script:a.sh} x", ("a.sh",)), [])

    def test_a_missing_script_warns(self):
        got = self.lint("{script:a.sh} x")
        self.assertTrue(any("a.sh" in m and "無い" in m for m in got), got)

    def test_a_bad_name_warns(self):
        got = self.lint("{script:a b} x")
        self.assertTrue(any("不正" in m for m in got), got)


class DirectNameWarningTest(unittest.TestCase):
    def test_with_a_root_the_full_command_is_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = ticket_ids.direct_name_warning("topic", root=tmp)
            self.assertIn(f"`{settings.script_command(tmp, 'ccnavi-worktree.sh')} <名前>`", text)
            self.assertNotIn("worktree add", text)

    def test_without_a_root_it_is_the_script_name_only(self):
        text = ticket_ids.direct_name_warning("topic")
        self.assertIn("`ccnavi-worktree.sh <名前>`", text)
        self.assertEqual(ticket_ids.direct_name_warning("feature-12-topic"), "")


class FinalFilesTest(unittest.TestCase):
    """できあがりの rules.yml・CCNAVI.md・docs の中身。"""

    def rules(self) -> dict:
        with open(final(".ccnavi/config/common/rules.yml"), encoding="utf-8") as f:
            return yaml.safe_load(f)

    def rule(self, rule_id: str) -> dict:
        for group in ("deny", "ask", "allow"):
            for r in self.rules().get(group, []):
                if r["id"] == rule_id:
                    return r
        raise AssertionError(rule_id)

    def test_workspace_root_message_points_at_the_worktree_script_and_the_docs(self):
        text = " ".join(self.rule("workspace-root")["message"].split())
        self.assertIn("{script:ccnavi-worktree.sh} <名前>", text)
        self.assertIn("worktree-merge.md「直接作業のワークツリーを取り込む」", text)
        self.assertIn("worktree.md「直接作業のワークツリーの名前」", text)
        self.assertIn("cd projects/<プロジェクト名> && {script:ccnavi-worktree.sh} <名前>", text)
        self.assertNotIn("ff-only", text)

    def test_the_worktree_add_hint_has_no_ff_only_procedure(self):
        text = " ".join(self.rule("ccnavi-git-worktree-add")["additionalContextOnce"].split())
        self.assertIn("{script:ccnavi-worktree.sh} <名前>", text)
        self.assertIn("--no-track", text)
        self.assertNotIn("ff-only", text)

    def test_no_ff_only_cutting_procedure_is_left_in_the_docs_or_rules(self):
        paths = [final(".ccnavi/config/common/rules.yml")]
        docs = os.path.join(ROOT, ".ccnavi", "docs")
        paths += [os.path.join(docs, n) for n in os.listdir(docs) if n.endswith(".md")]
        for path in paths:
            with self.subTest(path=os.path.basename(path)), open(path, encoding="utf-8") as f:
                self.assertNotIn("merge --ff-only", f.read())

    def test_the_test_command_speaks_the_quoted_script_and_the_docs(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = os.path.join(os.path.realpath(tmp), "a b")
            home = os.path.join(ws, ".ccnavi", "config", "common")
            os.makedirs(home)
            shutil.copy(final(".ccnavi/config/common/rules.yml"), home)
            os.makedirs(os.path.join(ws, "src"))
            done = run_ccnavi(
                ["--root", ws, "--test", "Write", f"{ws}/src/x.py", "--json"], input="", cwd=ws
            )
            import json

            response = json.loads(done.stdout)["response"]
        self.assertIn(settings.script_command(ws, "ccnavi-worktree.sh"), response)
        self.assertIn("worktree-merge.md", response)
        self.assertNotIn("ff-only", response)

    def test_ccnavi_md_line_is_a_one_line_replacement(self):
        with open(final(".ccnavi/CCNAVI.md"), encoding="utf-8") as f:
            text = f.read()
        line = next(ln for ln in text.splitlines() if "{{worktree_sh}}" in ln)
        old = (
            "  - 編集する前にワークツリー（{{worktrees}}/<名前>）を切り、その中で編集する。"
            "切り方は ccnavi-git.sh の --help（worktree add）。直接作業の名前は "
            "<先頭の語>-<番号>-<slug>（.ccnavi/docs/worktree.md）。"
        )
        self.assertLess(len(line) - len(old), 40)
        self.assertNotIn("--help（worktree add）", text)
        self.assertEqual(text.count("{{worktree_sh}}"), 1)

    def test_worktree_sh_expands_like_script_command(self):
        self.assertIn("worktree_sh", ccnavimd.SUBSTITUTIONS)
        with tempfile.TemporaryDirectory() as tmp:
            values = ccnavimd.values_for(settings.Settings(), tmp)
            self.assertEqual(
                values["worktree_sh"], settings.script_command(tmp, "ccnavi-worktree.sh")
            )


class NoRawGitInNudgeTest(unittest.TestCase):
    def test_the_nudge_has_no_git_outside_the_wrapper(self):
        from ccnavi.tickets import ops_stop_integrate as osi

        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.realpath(tmp)
            found = osi.Direct(
                "scratch",
                root + "/.claude/worktrees/scratch",
                "h",
                True,
                1,
                "main",
                integ_tree=root,
            )
            text = osi.integrate_nudge(root, found)
        for hit in re.finditer(r"(?<![\w./-])git(?![\w.-])", text.replace("ccnavi-git.sh", "")):
            self.fail(
                f"ラッパーを通らない git: {text[max(0, hit.start() - 20) : hit.end() + 20]!r}"
            )


if __name__ == "__main__":
    unittest.main()
