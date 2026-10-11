"""ccnavi-stage.sh の受入テスト。使い捨てのワークスペースを組み立てて sh を外から呼ぶ。

確かめるのは 4 つ。元ファイルが wip/scratchpad/ の同じ階層へ cp されること。既にある下書きを
上書きしないこと。ワークスペースの外や下書きの置き場の中を断ること。--commands が、元と違う
下書きにだけフルパスの mv を出すこと。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from tests import ROOT, common_sh

SHELL = shutil.which("sh") or shutil.which("bash")
SH_DIR = os.path.join(ROOT, os.environ.get("CCNAVI_SH_DIR", "") or ".ccnavi/scripts")


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class StageShTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = os.path.join(self._tmp.name, "ws")
        scripts = os.path.join(self.ws, ".ccnavi", "scripts")
        os.makedirs(scripts)
        for name in ("ccnavi-stage.sh", *common_sh(SH_DIR)):
            shutil.copy(os.path.join(SH_DIR, name), scripts)
        write(os.path.join(self.ws, ".ccnavi", "common", "rules.yml"), "version: 1\n")
        write(os.path.join(self.ws, ".claude", "hooks", "lint-py.sh"), "echo lint\n")
        self.rules = os.path.join(self.ws, ".ccnavi", "common", "rules.yml")
        self.hook = os.path.join(self.ws, ".claude", "hooks", "lint-py.sh")
        self.draft_rules = os.path.join(
            self.ws, "wip", "scratchpad", ".ccnavi", "common", "rules.yml"
        )
        self.draft_hook = os.path.join(
            self.ws, "wip", "scratchpad", ".claude", "hooks", "lint-py.sh"
        )

    def stage(self, *args, cwd=None):
        env = dict(os.environ)
        env.pop("CCNAVI_WORKSPACE", None)  # 実際のワークスペースを指させない
        script = os.path.join(self.ws, ".ccnavi", "scripts", "ccnavi-stage.sh").replace(os.sep, "/")
        return subprocess.run(
            [SHELL, script, *args],
            cwd=cwd or self.ws,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def test_copies_with_the_same_layout(self):
        result = self.stage(".ccnavi/common/rules.yml", ".claude/hooks/lint-py.sh")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("version: 1\n", read(self.draft_rules))
        self.assertEqual("echo lint\n", read(self.draft_hook))
        self.assertEqual("version: 1\n", read(self.rules), "元は触らない")

    def test_relative_to_cwd_and_absolute_paths(self):
        result = self.stage("common/rules.yml", cwd=os.path.join(self.ws, ".ccnavi"))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(os.path.isfile(self.draft_rules))
        result = self.stage(self.hook.replace(os.sep, "/"))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(os.path.isfile(self.draft_hook))

    def test_does_not_overwrite_an_edited_draft(self):
        self.stage(".ccnavi/common/rules.yml")
        write(self.draft_rules, "version: 2\n")
        result = self.stage(".ccnavi/common/rules.yml")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("上書きしない", result.stdout)
        self.assertEqual("version: 2\n", read(self.draft_rules))

    def test_refuses_paths_it_cannot_stage(self):
        outside = os.path.join(self._tmp.name, "outside.txt")
        write(outside, "x")
        for path, why in (
            (outside, "の下にありません"),
            (".ccnavi/common/nothing.yml", "実在するファイルではありません"),
            ("wip/scratchpad/x.md", "下書きの置き場の中"),
        ):
            with self.subTest(path=path):
                write(os.path.join(self.ws, "wip", "scratchpad", "x.md"), "x")
                result = self.stage(path)
                self.assertEqual(2, result.returncode)
                self.assertIn(why, result.stderr)
        self.assertFalse(os.path.exists(self.draft_rules))

    def test_refuses_quote_in_name(self):
        write(os.path.join(self.ws, "it's.md"), "x")
        result = self.stage("it's.md")
        self.assertEqual(2, result.returncode)
        self.assertIn("引用符", result.stderr)

    def test_commands_print_full_path_mv_for_edited_drafts_only(self):
        self.stage(".ccnavi/common/rules.yml", ".claude/hooks/lint-py.sh")
        write(self.draft_rules, "version: 2\n")
        result = self.stage("--commands", ".ccnavi/common/rules.yml", ".claude/hooks/lint-py.sh")
        mvs = [line for line in result.stdout.splitlines() if line.startswith("mv ")]
        self.assertEqual([f"mv '{self.draft_rules}' '{self.rules}'".replace(os.sep, "/")], mvs)
        self.assertEqual(
            1, result.returncode, "編集していない下書きが残るので、揃っていないと知らせる"
        )
        self.assertIn("まだ編集していない", result.stderr)
        self.assertEqual(
            "version: 1\n", read(self.rules), "差し替えはユーザの手。スクリプトは動かさない"
        )

    def test_commands_all_edited_succeeds(self):
        self.stage(".ccnavi/common/rules.yml", ".claude/hooks/lint-py.sh")
        write(self.draft_rules, "version: 2\n")
        write(self.draft_hook, "echo lint2\n")
        result = self.stage("--commands", ".ccnavi/common/rules.yml", ".claude/hooks/lint-py.sh")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(2, len([ln for ln in result.stdout.splitlines() if ln.startswith("mv ")]))

    def test_commands_without_draft_or_edit_prints_nothing(self):
        result = self.stage("--commands", ".ccnavi/common/rules.yml")
        self.assertEqual(1, result.returncode)
        self.assertIn("下書きがまだ無い", result.stderr)
        self.assertNotIn("mv ", result.stdout)

    def test_unknown_option_and_no_args(self):
        self.assertEqual(2, self.stage("--bogus").returncode)
        self.assertEqual(2, self.stage().returncode)
        self.assertEqual(0, self.stage("--help").returncode)


if __name__ == "__main__":
    unittest.main()
