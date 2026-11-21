"""ccnavi-stage.sh の受入テスト。使い捨てのワークスペースを組み立てて sh を外から呼ぶ。

確かめるのは 4 つ。元ファイルが wip/handoff/ の同じ階層へ cp されること。既にある下書きを
上書きしないこと。ワークスペースの外や下書きの置き場の中を断ること。--commands が、元と違う
下書きだけに、取り込みの手順（ccnavi-user-handoff.sh。defense を含む全種類）を出し、
mv を出さないこと。
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest

from tests import ROOT, common_sh, exe, put_messages

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
        put_messages(self.ws)
        self.exe = os.path.join(self._tmp.name, "bin", "ccnavi")
        write(
            self.exe,
            f"#!/bin/sh\nexec '{exe.sh_path()}' \"$@\"\n",
        )
        os.chmod(self.exe, os.stat(self.exe).st_mode | stat.S_IXUSR)
        write(os.path.join(self.ws, ".ccnavi", "config", "common", "rules.yml"), "version: 1\n")
        write(os.path.join(self.ws, ".claude", "hooks", "lint-py.sh"), "echo lint\n")
        self.rules = os.path.join(self.ws, ".ccnavi", "config", "common", "rules.yml")
        self.hook = os.path.join(self.ws, ".claude", "hooks", "lint-py.sh")
        self.draft_rules = os.path.join(
            self.ws, "wip", "handoff", ".ccnavi", "config", "common", "rules.yml"
        )
        self.draft_hook = os.path.join(
            self.ws, "wip", "handoff", "handoff-.claude", "hooks", "lint-py.sh"
        )

    def stage(self, *args, cwd=None):
        env = dict(os.environ)
        env.pop("CCNAVI_WORKSPACE", None)  # 実際のワークスペースを指させない
        env["CCNAVI_ENTRY_PATH"] = self.exe
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
        result = self.stage(".ccnavi/config/common/rules.yml", ".claude/hooks/lint-py.sh")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("version: 1\n", read(self.draft_rules))
        self.assertEqual("echo lint\n", read(self.draft_hook))
        self.assertEqual("version: 1\n", read(self.rules), "元は触らない")

    def test_inside_a_worktree_the_draft_goes_to_that_worktrees_handoff(self):
        """cwd が .claude/worktrees/<名前> の中なら、下書きはそのワークツリーの wip/handoff/ に置く
        （ブランチに載るので push でき、直接作業なら integrate が外す）。
        取り込みの手順はそのワークツリーを --root に指す。"""
        wt = os.path.join(self.ws, ".claude", "worktrees", "scratch-1-x")
        os.makedirs(os.path.join(wt, "src"))
        write(os.path.join(wt, ".ccnavi", "config", "common", "rules.yml"), "version: 1\n")
        r = self.stage(self.rules, cwd=os.path.join(wt, "src"))
        self.assertEqual(0, r.returncode, r.stderr)
        draft = os.path.join(wt, "wip", "handoff", ".ccnavi", "config", "common", "rules.yml")
        self.assertTrue(os.path.isfile(draft), r.stdout)
        self.assertFalse(os.path.exists(self.draft_rules))
        write(draft, "version: 2\n")
        r = self.stage("--commands", self.rules, cwd=wt)
        self.assertEqual(0, r.returncode, r.stderr)
        # mv ではなく、--root にワークツリーを指した取り込みの手順
        self.assertNotIn("mv ", r.stdout)
        self.assertIn(
            f"--root '{wt}' '.ccnavi/config/common/rules.yml'", r.stdout.replace(os.sep, "/")
        )
        # 下書きの置き場の中（ワークツリー側）は元として受けない。
        r = self.stage(draft, cwd=wt)
        self.assertEqual(2, r.returncode)
        self.assertIn("下書きの置き場の中", r.stderr)

    def test_the_next_step_names_the_staged_paths(self):
        result = self.stage(".ccnavi/config/common/rules.yml", ".claude/hooks/lint-py.sh")
        nexts = [line for line in result.stdout.splitlines() if line.startswith("次: ")]
        self.assertEqual(1, len(nexts), result.stdout)
        # `<パス>` のままにせず、いま下書きを作ったパスを埋める
        self.assertNotIn("<パス>", nexts[0])
        self.assertTrue(
            nexts[0].endswith(
                f"--commands {self.rules} {self.hook} を打つ（取り込みの手順が出る）"
            ),
            result.stdout,
        )
        # 下書きが既にあるだけでも、次の一手は消えない
        again = self.stage(".ccnavi/config/common/rules.yml")
        self.assertIn("次: ", again.stdout)

    def handoff_cmd(self):
        return f"sh '{self.ws}/.ccnavi/scripts/ccnavi-user-handoff.sh'".replace(os.sep, "/")

    def test_commands_print_handoff_not_mv_for_edited_defense_drafts(self):
        self.stage(".ccnavi/config/common/rules.yml", ".claude/hooks/lint-py.sh")
        write(self.draft_hook, "echo lint2\n")
        result = self.stage(
            "--commands", ".ccnavi/config/common/rules.yml", ".claude/hooks/lint-py.sh"
        )
        self.assertNotIn("mv ", result.stdout)
        self.assertIn(f"{self.handoff_cmd()} '.claude/hooks/lint-py.sh'\n", result.stdout)
        self.assertIn("--yes --expect <上のハッシュ>", result.stdout)
        self.assertEqual(
            1, result.returncode, "編集していない下書きが残るので、揃っていないと知らせる"
        )
        self.assertIn("まだ編集していない", result.stderr)
        # 揃っていないので、ユーザに案内する次の一手は出さない
        self.assertNotIn("次: ", result.stdout)
        self.assertEqual(
            "echo lint\n", read(self.hook), "差し替えはユーザの手。スクリプトは動かさない"
        )


if __name__ == "__main__":
    unittest.main()
