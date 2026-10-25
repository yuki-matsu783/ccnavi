"""ccnavi-stage.sh の受入テスト。使い捨てのワークスペースを組み立てて sh を外から呼ぶ。

確かめるのは 4 つ。元ファイルが wip/handoff/ の同じ階層へ cp されること。既にある下書きを
上書きしないこと。ワークスペースの外や下書きの置き場の中を断ること。--commands が、元と違う
下書きだけを、種類ごとに分けて出すこと（取り込める種類は ccnavi-user-handoff.sh の手順、defense は
フルパスの mv）。
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

from tests import ROOT, SRC, common_sh, put_messages

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
        for name in ("ccnavi-stage.sh", "ccnavi-handoff-names.txt", *common_sh(SH_DIR)):
            shutil.copy(os.path.join(SH_DIR, name), scripts)
        put_messages(self.ws)
        self.exe = os.path.join(self._tmp.name, "bin", "ccnavi")
        write(
            self.exe,
            f"#!/bin/sh\nPYTHONPATH='{SRC}' exec '{sys.executable}' -m ccnavi \"$@\"\n",
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
        r = self.stage(self.rules, cwd=os.path.join(wt, "src"))
        self.assertEqual(0, r.returncode, r.stderr)
        draft = os.path.join(wt, "wip", "handoff", ".ccnavi", "config", "common", "rules.yml")
        self.assertTrue(os.path.isfile(draft), r.stdout)
        self.assertFalse(os.path.exists(self.draft_rules))
        write(draft, "version: 2\n")
        r = self.stage("--commands", self.rules, cwd=wt)
        self.assertEqual(0, r.returncode, r.stderr)
        # 取り込める種類は mv ではなく、--root にワークツリーを指した取り込みの手順
        self.assertNotIn("mv ", r.stdout)
        self.assertIn(
            f"--root '{wt}' '.ccnavi/config/common/rules.yml'", r.stdout.replace(os.sep, "/")
        )
        # 下書きの置き場の中（ワークツリー側）は元として受けない。
        r = self.stage(draft, cwd=wt)
        self.assertEqual(2, r.returncode)
        self.assertIn("下書きの置き場の中", r.stderr)

    def test_inside_a_worktree_a_relative_path_gets_an_absolute_next_step(self):
        """cwd のワークツリーの中の相対パスで渡しても、「次:」は絶対パスで言う。
        どの cwd から打っても同じファイルを指す。"""
        wt = os.path.join(self.ws, ".claude", "worktrees", "scratch-1-x")
        inner = os.path.join(wt, ".ccnavi", "config", "common", "rules.yml")
        write(inner, "version: 1\n")
        r = self.stage(".ccnavi/config/common/rules.yml", cwd=wt)
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertIn(f"--commands {inner}", r.stdout)

    def test_a_linked_handoff_directory_is_refused(self):
        wt = os.path.join(self.ws, ".claude", "worktrees", "scratch-1-x")
        outside = os.path.join(self._tmp.name, "outside")
        os.makedirs(os.path.join(wt, "wip"))
        os.makedirs(outside)
        try:
            os.symlink(outside, os.path.join(wt, "wip", "handoff"))
        except (OSError, NotImplementedError):
            self.skipTest("シンボリックリンクを作れない")
        r = self.stage(self.rules, cwd=wt)
        self.assertEqual(2, r.returncode)
        self.assertIn("シンボリックリンク", r.stderr)
        self.assertEqual([], os.listdir(outside))

    def test_relative_to_cwd_and_absolute_paths(self):
        result = self.stage("config/common/rules.yml", cwd=os.path.join(self.ws, ".ccnavi"))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(os.path.isfile(self.draft_rules))
        result = self.stage(self.hook.replace(os.sep, "/"))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(os.path.isfile(self.draft_hook))

    def test_the_next_step_names_the_staged_paths(self):
        result = self.stage(".ccnavi/config/common/rules.yml", ".claude/hooks/lint-py.sh")
        nexts = [line for line in result.stdout.splitlines() if line.startswith("次: ")]
        self.assertEqual(1, len(nexts), result.stdout)
        # `<パス>` のままにせず、いま下書きを作ったパスを埋める
        self.assertNotIn("<パス>", nexts[0])
        self.assertTrue(
            nexts[0].endswith(
                f"--commands {self.rules} {self.hook} を打つ"
                "（種類ごとに、取り込みの手順か差し替えの mv が出る）"
            ),
            nexts[0],
        )
        # 下書きが既にあるだけでも、次の一手は消えない
        again = self.stage(".ccnavi/config/common/rules.yml")
        self.assertIn("次: ", again.stdout)

    def test_does_not_overwrite_an_edited_draft(self):
        self.stage(".ccnavi/config/common/rules.yml")
        write(self.draft_rules, "version: 2\n")
        result = self.stage(".ccnavi/config/common/rules.yml")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("上書きしない", result.stdout)
        self.assertEqual("version: 2\n", read(self.draft_rules))

    def test_refuses_paths_it_cannot_stage(self):
        outside = os.path.join(self._tmp.name, "outside.txt")
        write(outside, "x")
        for path, why in (
            (outside, "の下にありません"),
            (".ccnavi/config/common/nothing.yml", "実在するファイルではありません"),
            ("wip/handoff/x.md", "下書きの置き場の中"),
        ):
            with self.subTest(path=path):
                write(os.path.join(self.ws, "wip", "handoff", "x.md"), "x")
                result = self.stage(path)
                self.assertEqual(2, result.returncode)
                self.assertIn(why, result.stderr)
        self.assertFalse(os.path.exists(self.draft_rules))

    def test_refuses_quote_in_name(self):
        write(os.path.join(self.ws, "it's.md"), "x")
        result = self.stage("it's.md")
        self.assertEqual(2, result.returncode)
        self.assertIn("引用符", result.stderr)

    def test_commands_print_full_path_mv_for_edited_defense_drafts_only(self):
        self.stage(".ccnavi/config/common/rules.yml", ".claude/hooks/lint-py.sh")
        write(self.draft_hook, "echo lint2\n")
        result = self.stage(
            "--commands", ".ccnavi/config/common/rules.yml", ".claude/hooks/lint-py.sh"
        )
        mvs = [line for line in result.stdout.splitlines() if line.startswith("mv ")]
        self.assertEqual([f"mv '{self.draft_hook}' '{self.hook}'".replace(os.sep, "/")], mvs)
        self.assertEqual(
            1, result.returncode, "編集していない下書きが残るので、揃っていないと知らせる"
        )
        self.assertIn("まだ編集していない", result.stderr)
        # 揃っていないので、ユーザに案内する次の一手は出さない
        self.assertNotIn("次: ", result.stdout)
        self.assertEqual(
            "echo lint\n", read(self.hook), "差し替えはユーザの手。スクリプトは動かさない"
        )

    def test_commands_all_edited_defense_succeeds(self):
        self.stage(".claude/hooks/lint-py.sh")
        write(self.draft_hook, "echo lint2\n")
        result = self.stage("--commands", ".claude/hooks/lint-py.sh")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(1, len([ln for ln in result.stdout.splitlines() if ln.startswith("mv ")]))
        self.assertNotIn("ccnavi-user-handoff.sh", result.stdout)
        nexts = [line for line in result.stdout.splitlines() if line.startswith("次: ")]
        self.assertEqual(1, len(nexts), result.stdout)
        self.assertIn("mvをユーザに案内してターンを終える", nexts[0])

    def test_commands_point_importable_kinds_to_user_handoff_not_mv(self):
        write(
            os.path.join(self.ws, ".ccnavi", "config", "common", "templates", "quickfix.yml"),
            "a: 1\n",
        )
        draft = os.path.join(
            self.ws, "wip", "handoff", ".ccnavi", "config", "common", "templates", "quickfix.yml"
        )
        self.stage(".ccnavi/config/common/templates/quickfix.yml")
        write(draft, "a: 2\n")
        result = self.stage("--commands", ".ccnavi/config/common/templates/quickfix.yml")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("mv ", result.stdout)
        handoff = f"{self.ws}/.ccnavi/scripts/ccnavi-user-handoff.sh".replace(os.sep, "/")
        self.assertIn(
            f"sh '{handoff}' '.ccnavi/config/common/templates/quickfix.yml'\n", result.stdout
        )
        self.assertIn("--yes --expect <上のハッシュ>", result.stdout)
        nexts = [line for line in result.stdout.splitlines() if line.startswith("次: ")]
        self.assertEqual(1, len(nexts), result.stdout)
        self.assertIn("取り込みの手順をユーザに案内してターンを終える", nexts[0])

    def test_commands_split_mixed_kinds(self):
        self.stage(".ccnavi/config/common/rules.yml", ".claude/hooks/lint-py.sh")
        write(self.draft_rules, "version: 2\n")
        write(self.draft_hook, "echo lint2\n")
        result = self.stage(
            "--commands", ".ccnavi/config/common/rules.yml", ".claude/hooks/lint-py.sh"
        )
        self.assertEqual(0, result.returncode, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(1, len([ln for ln in lines if ln.startswith("mv ")]))
        self.assertIn(f"mv '{self.draft_hook}' '{self.hook}'".replace(os.sep, "/"), lines)
        self.assertEqual(
            1, len([ln for ln in lines if "ccnavi-user-handoff.sh" in ln and "--yes" not in ln])
        )
        self.assertNotIn(f"mv '{self.draft_rules}'".replace(os.sep, "/"), result.stdout)
        nexts = [ln for ln in lines if ln.startswith("次: ")]
        self.assertEqual(1, len(nexts), result.stdout)
        self.assertIn("取り込みの手順 と mv", nexts[0])

    def test_importable_classification_matches_the_python_kinds(self):
        """sh の is_importable が、実行ファイルの KINDS と同じ答えを返す（表のずれを見つける）。"""
        from ccnavi.tickets import handoff

        paths = [
            ".ccnavi/config/common/templates/a.yml",
            ".ccnavi/messages/a.yml",
            ".ccnavi/messages/sub/a.yml",
            "projects/p/.ccnavi/messages/a.yml",
            ".ccnavi/config/common/messages/deny.yml",  # 旧い置き場。取り込めない
            ".ccnavi/config/project/templates/a.yml",
            ".ccnavi/config/project/templates/sub/a.yml",
            ".ccnavi/config/common/templates/x/templates/a.yml",
            ".ccnavi/docs/.md",
            "projects/p/.ccnavi/config/project/templates/a.yml",
            ".ccnavi/config/common/rules.yml",
            ".ccnavi/config/project/risks.yml",
            "projects/p/.ccnavi/config/project/phases.yml",
            "projects/p/.ccnavi/config/common/rules.yml",
            ".ccnavi/docs/a/b.md",
            ".ccnavi/CCNAVI.md",
            ".ccnavi/scripts/ccnavi-stage.sh",
            ".claude/hooks/lint-py.sh",
            ".claude/settings.json",
            ".claude/skills/x/SKILL.md",
            "README.md",
        ]
        script = os.path.join(SH_DIR, "ccnavi-stage.sh")
        # 保護対象の下書き（取り込み前）があれば、取り込んだ後の姿として下書きを読む。
        draft = os.path.join(ROOT, "wip", "handoff", ".ccnavi", "scripts", "ccnavi-stage.sh")
        if os.path.isfile(draft):
            script = draft
        with open(script, encoding="utf-8") as f:
            text = f.read()
        start = text.index("is_importable() {")
        func = text[start : text.index("\n}\n", start) + 3]
        for rel in paths:
            with self.subTest(rel=rel):
                kind = handoff.kind_of(handoff.key_of(rel))
                want = kind is not None and kind.id != handoff.KIND_DEFENSE
                got = (
                    subprocess.run(
                        [SHELL, "-c", f"{func}\nis_importable '{rel}'"], capture_output=True
                    ).returncode
                    == 0
                )
                self.assertEqual(want, got)

    def test_commands_without_draft_or_edit_prints_nothing(self):
        result = self.stage("--commands", ".ccnavi/config/common/rules.yml")
        self.assertEqual(1, result.returncode)
        self.assertIn("下書きがまだ無い", result.stderr)
        self.assertNotIn("mv ", result.stdout)

    def test_refuses_symlinks_that_leave_the_workspace(self):
        outside = os.path.join(self._tmp.name, "outside")
        write(os.path.join(outside, "f"), "secret")
        try:
            os.symlink(outside, os.path.join(self.ws, "link"), target_is_directory=True)
            os.symlink(self.rules, os.path.join(self.ws, "rules-link.yml"))
        except (OSError, NotImplementedError):
            self.skipTest("シンボリックリンクが作れない")
        for path, why in (("link/f", "外"), ("rules-link.yml", "シンボリックリンク")):
            with self.subTest(path=path):
                result = self.stage(path)
                self.assertEqual(2, result.returncode, result.stdout)
                self.assertIn(why, result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "wip", "handoff", "link")))

    def test_commands_refuse_when_the_original_changed_after_staging(self):
        self.stage(".ccnavi/config/common/rules.yml")
        write(self.draft_rules, "version: 2\n")
        write(self.rules, "version: 3\n")  # 別のセッションかユーザが元を直した
        result = self.stage("--commands", ".ccnavi/config/common/rules.yml")
        self.assertEqual(1, result.returncode)
        self.assertNotIn("mv ", result.stdout)
        self.assertIn("元のファイルが変わっている", result.stderr)
        self.assertEqual("version: 3\n", read(self.rules))

    def test_unknown_option_and_no_args(self):
        self.assertEqual(2, self.stage("--bogus").returncode)
        self.assertEqual(2, self.stage().returncode)
        self.assertEqual(0, self.stage("--help").returncode)


if __name__ == "__main__":
    unittest.main()
