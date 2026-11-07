"""ccnavi-clean.sh の受入テスト。使い捨てのワークスペースを組み立てて sh を外から呼ぶ。

確かめるのは 3 つ。決まった名前の生成物だけが消えること。`.claude/worktrees/` の直下の
名前以外は受け付けないこと。未コミットの変更があるワークツリーでは何も消さないこと。

消すのは ccnavi の実行ファイル（`worktree clean`・`worktree drop`）。
実行ファイルは tests/exe.py が組み立てたものを起こす（CCNAVI_ENTRY_PATH）。

読み返すのは終了コード・出力と、ファイルシステムに残ったものだけ。

`CCNAVI_SH_DIR` で、コピーする sh の出どころを差し替えられる。既定はこのツリーの
`.ccnavi/scripts/`（テストしているソースそのもの）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from tests import ROOT, common_sh, exe, put_messages

SHELL = shutil.which("sh") or shutil.which("bash")
GIT = shutil.which("git")
SH_DIR = os.path.join(ROOT, os.environ.get("CCNAVI_SH_DIR", "") or ".ccnavi/scripts")
SCRIPTS = (
    "ccnavi-clean.sh",
    # 残った子のブランチを消す入口
    "ccnavi-user-clean-branch.sh",
    *common_sh(SH_DIR),
)
# Windows は chmod で消せなくならず、root は権限を無視して消す。
CANNOT_LOCK = os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0)


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def files_under(path):
    out = []
    for base, _dirs, names in os.walk(path):
        for name in names:
            out.append(os.path.relpath(os.path.join(base, name), path).replace(os.sep, "/"))
    return sorted(out)


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def link_dir(target, link):
    """ディレクトリへのリンクを作る。Windows で symlink が作れなければ junction。"""
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except OSError:
        pass
    try:
        import _winapi

        _winapi.CreateJunction(target, link)
        return True
    except (ImportError, OSError):
        return False


class CleanBase:
    """使い捨てのワークスペース。実行ファイルは、tests/exe.py が組み立てたものを起こす shim。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        # tearDown ではなく addCleanup で消す。テストが足す片付け（権限の戻し）が先に走る。
        self.addCleanup(self._tmp.cleanup)
        self.ws = os.path.join(self._tmp.name, "ws")
        scripts = os.path.join(self.ws, ".ccnavi", "scripts")
        os.makedirs(scripts)
        for name in SCRIPTS:
            shutil.copy(os.path.join(SH_DIR, name), scripts)
        self.worktrees = os.path.join(self.ws, ".claude", "worktrees")
        os.makedirs(self.worktrees)
        self.bin = os.path.join(self._tmp.name, "bin", "ccnavi")
        write(
            self.bin,
            f"#!/bin/sh\nexec '{exe.sh_path()}' \"$@\"\n",
        )
        os.chmod(self.bin, 0o755)

    def run_clean(self, *args, cwd=None):
        env = dict(os.environ)
        env.pop("CCNAVI_WORKSPACE", None)  # 実際のワークスペースを指させない
        env["CCNAVI_ENTRY_PATH"] = self.bin
        script = os.path.join(self.ws, ".ccnavi", "scripts", "ccnavi-clean.sh").replace(os.sep, "/")
        return subprocess.run(
            [SHELL, script, *args],
            cwd=cwd or self.ws,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def make_shell(self, name):
        """git の登録が外れたディレクトリ。生成物と、消えてはいけないものを混ぜて置く。"""
        top = os.path.join(self.worktrees, name)
        write(os.path.join(top, "src", "a.txt"), "keep")
        write(os.path.join(top, "node_modules", ".pnpm", "x", "index.js"))
        write(os.path.join(top, "ext", "package.json"), "{}")
        write(os.path.join(top, "ext", "out", "extension.js"))
        write(os.path.join(top, "ext", "out", "node_modules", "z", "index.js"))  # out ごと消える
        # 並べると ext/out と ext/out/node_modules の間に来る。
        write(os.path.join(top, "ext", "out-x", "node_modules", "w", "index.js"))
        write(os.path.join(top, "ext", "node_modules", "y", "index.js"))
        write(os.path.join(top, "ext", "dist", "bundle.js"))
        write(os.path.join(top, "ext", ".cache", "c"))
        write(os.path.join(top, "docs", "out", "keep.txt"), "keep")  # package.json の隣ではない
        write(os.path.join(top, "docs", "dist", "keep.txt"), "keep")
        write(os.path.join(top, ".venv", "pyvenv.cfg"))
        write(os.path.join(top, "pkg", "__pycache__", "m.pyc"))
        write(os.path.join(top, ".pytest_cache", "v", "cache"))
        write(os.path.join(top, ".ruff_cache", "0.16.6", "123", "abc"))
        return top


@unittest.skipUnless(SHELL, "sh が要る")
class CleanCases(CleanBase, unittest.TestCase):
    """生成物だけを消す側（`ccnavi-clean.sh <名前>`）の検査。"""

    def test_removes_only_generated_directories(self):
        top = self.make_shell("shell")
        result = self.run_clean("shell")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            files_under(top),
            ["docs/dist/keep.txt", "docs/out/keep.txt", "ext/package.json", "src/a.txt"],
        )
        self.assertIn("removed node_modules", result.stdout)
        self.assertIn("removed ext/out\n", result.stdout)
        self.assertIn("removed ext/dist\n", result.stdout)
        self.assertIn("removed ext/.cache\n", result.stdout)
        self.assertIn("removed ext/out-x/node_modules", result.stdout)
        self.assertNotIn("ext/out/node_modules", result.stdout)

    def test_workspace_root_is_found_from_inside_a_worktree(self):
        top = self.make_shell("shell")
        result = self.run_clean("shell", cwd=os.path.join(top, "src"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.exists(os.path.join(top, "node_modules")))

    def test_dry_run_lists_and_keeps_everything(self):
        top = self.make_shell("shell")
        before = files_under(top)
        result = self.run_clean("shell", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("would remove node_modules", result.stdout)
        self.assertIn("would remove .venv", result.stdout)
        self.assertEqual(files_under(top), before)

    def test_nothing_to_remove(self):
        write(os.path.join(self.worktrees, "empty", "a.txt"))
        result = self.run_clean("empty")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("削除するものはありません", result.stdout)

    def test_rejects_anything_but_a_name(self):
        # 置き場の外に、消えてはいけない生成物を置いておく。
        outside = os.path.join(self.ws, "node_modules", "keep.js")
        write(outside)
        self.make_shell("shell")
        for arg in ("..", ".", "../..", "shell/src", ".claude/worktrees/shell", "..\\..", "C:x"):
            with self.subTest(arg=arg):
                result = self.run_clean(arg)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertTrue(os.path.exists(outside))

    def test_rejects_missing_name_and_unknown_option(self):
        self.make_shell("shell")
        for args in ((), ("nothing-here",), ("shell", "--force"), ("shell", "other")):
            with self.subTest(args=args):
                result = self.run_clean(*args)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.worktrees, "shell", "node_modules")))

    def test_does_not_follow_links(self):
        precious = os.path.join(self._tmp.name, "precious")
        write(os.path.join(precious, "node_modules", "keep.js"))
        write(os.path.join(precious, "out", "keep.js"))
        top = self.make_shell("shell")
        if not link_dir(precious, os.path.join(top, "linked")):
            self.skipTest("リンクが作れない")
        # 生成物の名前をしたリンクは、リンクだけが消えて先は残る。
        link_dir(os.path.join(precious, "out"), os.path.join(top, "ext", "out-link"))
        link_dir(os.path.join(precious, "node_modules"), os.path.join(top, "src", "node_modules"))
        result = self.run_clean("shell")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.exists(os.path.join(precious, "node_modules", "keep.js")))
        self.assertTrue(os.path.exists(os.path.join(precious, "out", "keep.js")))
        self.assertFalse(os.path.lexists(os.path.join(top, "src", "node_modules")))

    def test_rejects_worktree_directory_that_is_a_link(self):
        precious = os.path.join(self._tmp.name, "precious")
        write(os.path.join(precious, "node_modules", "keep.js"))
        if not link_dir(precious, os.path.join(self.worktrees, "linked")):
            self.skipTest("リンクが作れない")
        result = self.run_clean("linked")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertTrue(os.path.exists(os.path.join(precious, "node_modules", "keep.js")))

    @unittest.skipIf(CANNOT_LOCK, "権限で消せない状態が作れない")
    def test_reports_what_could_not_be_removed(self):
        top = self.make_shell("shell")
        locked = os.path.join(top, "node_modules", ".pnpm")
        os.chmod(locked, 0o555)
        self.addCleanup(os.chmod, locked, 0o755)
        result = self.run_clean("shell")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("node_modules", result.stderr)
        self.assertIn("削除しきれませんでした", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(top, ".venv")))  # 他は消す

    @unittest.skipUnless(GIT, "git が要る")
    def test_stops_on_uncommitted_changes_then_cleans_when_clean(self):
        git(self.ws, "init", "-q", "-b", "main")
        git(self.ws, "config", "user.email", "t@example.invalid")
        git(self.ws, "config", "user.name", "t")
        write(os.path.join(self.ws, ".gitignore"), "node_modules/\n.claude/worktrees/\n")
        git(self.ws, "add", ".gitignore")
        git(self.ws, "commit", "-q", "-m", "seed")
        git(self.ws, "worktree", "add", "-q", ".claude/worktrees/wt", "-b", "wt", "main")
        top = os.path.join(self.worktrees, "wt")
        write(os.path.join(top, "node_modules", "x.js"))
        write(os.path.join(top, "draft.txt"), "書きかけ")

        result = self.run_clean("wt")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("draft.txt", result.stderr)
        self.assertTrue(os.path.exists(os.path.join(top, "node_modules", "x.js")))

        os.remove(os.path.join(top, "draft.txt"))
        result = self.run_clean("wt")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.exists(os.path.join(top, "node_modules")))


@unittest.skipUnless(SHELL and GIT, "sh と git が要る")
class CleanGitStateTest(CleanBase, unittest.TestCase):
    """`.git` があるときの確かめ。読めない・指す先が消えている、の扱いを分ける。"""

    def test_a_gitfile_pointing_nowhere_is_treated_as_a_leftover(self):
        top = self.make_shell("shell")
        write(os.path.join(top, ".git"), "gitdir: /nonexistent/gitdir\n")
        result = self.run_clean("shell")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("git の登録が残っていない", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(top, "node_modules")))

    def test_an_unreadable_git_state_stops_without_removing_anything(self):
        top = self.make_shell("shell")
        os.makedirs(os.path.join(top, ".git"))  # 中身の無い .git。git status は失敗する
        before = files_under(top)
        result = self.run_clean("shell")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("git の状態を読めない", result.stderr)
        self.assertEqual(files_under(top), before)

    def test_a_missing_executable_is_an_environment_error(self):
        self.make_shell("shell")
        env = {**os.environ, "CCNAVI_ENTRY_PATH": os.path.join(self._tmp.name, "none")}
        env.pop("CCNAVI_WORKSPACE", None)
        script = os.path.join(self.ws, ".ccnavi", "scripts", "ccnavi-clean.sh")
        result = subprocess.run(
            [SHELL, script, "shell"],
            cwd=self.ws,
            env={**env, "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("実行ファイルが見つかりません", result.stderr)


@unittest.skipUnless(SHELL and GIT, "sh と git が要る")
class WorktreeOptionTest(unittest.TestCase):
    """`--worktree <名前>`。掃除とワークツリーの削除を実行ファイル（`worktree drop`）に任せる。

    実行ファイルは tests/exe.py が組み立てたものを起こす（CCNAVI_ENTRY_PATH）。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = os.path.join(self._tmp.name, "ws")
        scripts = os.path.join(self.ws, ".ccnavi", "scripts")
        os.makedirs(scripts)
        for name in SCRIPTS:
            shutil.copy(os.path.join(SH_DIR, name), scripts)
        self.bin = os.path.join(self._tmp.name, "bin", "ccnavi")
        write(
            self.bin,
            f"#!/bin/sh\nexec '{exe.sh_path()}' \"$@\"\n",
        )
        os.chmod(self.bin, 0o755)
        put_messages(self.ws)
        git(self.ws, "init", "-q", "-b", "main")
        git(self.ws, "config", "user.email", "t@example.invalid")
        git(self.ws, "config", "user.name", "t")
        write(os.path.join(self.ws, ".gitignore"), "node_modules/\n.claude/worktrees/\nlogs/\n")
        git(self.ws, "add", ".gitignore")
        git(self.ws, "commit", "-q", "-m", "seed")
        git(self.ws, "worktree", "add", "-q", ".claude/worktrees/wt", "-b", "wt", "main")
        self.top = os.path.join(self.ws, ".claude", "worktrees", "wt")
        write(os.path.join(self.top, "node_modules", "x.js"))

    def run_clean(self, *args, cwd=None, name="ccnavi-clean.sh"):
        env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        env.pop("CLAUDE_PROJECT_DIR", None)
        env["CCNAVI_ENTRY_PATH"] = self.bin
        script = os.path.join(self.ws, ".ccnavi", "scripts", name).replace(os.sep, "/")
        return subprocess.run(
            [SHELL, script, *args],
            cwd=cwd or self.ws,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def test_removes_the_worktree_from_outside_and_keeps_the_branch(self):
        result = self.run_clean("--worktree", "wt")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ワークツリー wt を削除しました", result.stdout)
        self.assertFalse(os.path.exists(self.top))
        branches = subprocess.run(
            ["git", "branch", "--list", "wt"], cwd=self.ws, capture_output=True, text=True
        )
        self.assertEqual(branches.stdout.strip(), "wt")

    def test_keeps_the_worktree_when_the_cwd_is_inside(self):
        result = self.run_clean("--worktree", "wt", cwd=self.top)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("cwd がワークツリー wt の中にあるので削除しませんでした", result.stdout)
        self.assertIn("ccnavi-clean.sh --worktree wt", result.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.top, "node_modules", "x.js")))

    def test_rejects_paths_and_dry_run(self):
        for args in (("--worktree", "wt", "--dry-run"), ("--worktree", "../wt"), ("--worktree",)):
            with self.subTest(args=args):
                result = self.run_clean(*args)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertTrue(os.path.isdir(self.top))

    def clean_branch(self, *args):
        return self.run_clean(*args, name="ccnavi-user-clean-branch.sh")

    def test_branch_refuses_a_branch_ccnavi_did_not_record(self):
        # 記録に無いブランチは、確認の有無によらず消さない（git branch -D の代わりに使わせない）。
        for args in (("wt",), ("wt", "--confirmed"), ("--confirmed", "wt")):
            with self.subTest(args=args):
                result = self.clean_branch(*args)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("記録したブランチではない", result.stderr)
        branches = subprocess.run(
            ["git", "branch", "--list", "wt"], cwd=self.ws, capture_output=True, text=True
        )
        self.assertEqual(branches.stdout.strip().lstrip("+ ").strip(), "wt")

    def test_branch_options_are_checked(self):
        for args in (
            ("wt", "--worktree"),
            ("wt", "--dry-run"),
            ("wt", "other"),
            ("../wt",),
            ("--confirmed",),
            (),
        ):
            with self.subTest(args=args):
                result = self.clean_branch(*args)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_the_old_branch_mode_is_refused_as_an_unknown_option(self):
        # hook の止めが ask に下がっても、ccnavi-clean.sh の --branch と --confirmed は
        # 未知のオプションとして 2 で拒否し、何も消さない。
        for args in (
            ("--branch", "wt"),
            ("--branch", "wt", "--confirmed"),
            ("--confirmed", "wt"),
            ("--confirmed",),
            ("wt", "--branch"),
        ):
            with self.subTest(args=args):
                result = self.run_clean(*args)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("は指定できません", result.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.top, "node_modules", "x.js")))
        branches = subprocess.run(
            ["git", "branch", "--list", "wt"], cwd=self.ws, capture_output=True, text=True
        )
        self.assertEqual(branches.stdout.strip().lstrip("+ ").strip(), "wt")


if __name__ == "__main__":
    unittest.main()
