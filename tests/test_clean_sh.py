"""ccnavi-clean.sh の受入テスト。使い捨てのワークスペースを組み立てて sh を外から叩く。

確かめるのは 3 つ。決まった名前の生成物だけが消えること。`.claude/worktrees/` の直下の
名前以外は受け付けないこと。未コミットの変更がある作業ツリーでは何も消さないこと。

読み返すのは終了コード・出力と、ファイルシステムに残ったものだけ。

`CCNAVI_SH_DIR` で、写す sh の出どころを差し替えられる。既定はこのツリーの
`.claude/scripts/`（テストしているソースそのもの）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHELL = shutil.which("sh") or shutil.which("bash")
NODE = shutil.which("node")
GIT = shutil.which("git")
SH_DIR = os.path.join(ROOT, os.environ.get("CCNAVI_SH_DIR", "") or ".claude/scripts")
SCRIPTS = ("ccnavi-clean.sh", "ccnavi-clean.js", "ccnavi-common.sh")


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


@unittest.skipUnless(SHELL and NODE, "sh と node が要る")
class CleanTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = os.path.join(self._tmp.name, "ws")
        scripts = os.path.join(self.ws, ".claude", "scripts")
        os.makedirs(scripts)
        for name in SCRIPTS:
            shutil.copy(os.path.join(SH_DIR, name), scripts)
        self.worktrees = os.path.join(self.ws, ".claude", "worktrees")
        os.makedirs(self.worktrees)

    def tearDown(self):
        self._tmp.cleanup()

    def run_clean(self, *args, cwd=None):
        env = dict(os.environ)
        env.pop("CCNAVI_WORKSPACE", None)  # 本物のワークスペースを指させない
        script = os.path.join(self.ws, ".claude", "scripts", "ccnavi-clean.sh").replace(os.sep, "/")
        return subprocess.run(
            [SHELL, script, *args],
            cwd=cwd or self.ws,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def make_shell(self, name):
        """git に登録の無い抜け殻。生成物と、消えてはいけないものを混ぜて置く。"""
        top = os.path.join(self.worktrees, name)
        write(os.path.join(top, "src", "a.txt"), "keep")
        write(os.path.join(top, "node_modules", ".pnpm", "x", "index.js"))
        write(os.path.join(top, "ext", "package.json"), "{}")
        write(os.path.join(top, "ext", "out", "extension.js"))
        write(os.path.join(top, "ext", "node_modules", "y", "index.js"))
        write(os.path.join(top, "docs", "out", "keep.txt"), "keep")  # package.json の隣ではない
        write(os.path.join(top, ".venv", "pyvenv.cfg"))
        write(os.path.join(top, "pkg", "__pycache__", "m.pyc"))
        write(os.path.join(top, ".pytest_cache", "v", "cache"))
        return top

    def test_removes_only_generated_directories(self):
        top = self.make_shell("shell")
        result = self.run_clean("shell")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(files_under(top), ["docs/out/keep.txt", "ext/package.json", "src/a.txt"])
        self.assertIn("removed node_modules", result.stdout)
        self.assertIn("removed ext/out", result.stdout)

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
        self.assertIn("消すものはありません", result.stdout)

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
        top = self.make_shell("shell")
        if not link_dir(precious, os.path.join(top, "linked")):
            self.skipTest("リンクが作れない")
        result = self.run_clean("shell")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.exists(os.path.join(precious, "node_modules", "keep.js")))

    def test_rejects_worktree_directory_that_is_a_link(self):
        precious = os.path.join(self._tmp.name, "precious")
        write(os.path.join(precious, "node_modules", "keep.js"))
        if not link_dir(precious, os.path.join(self.worktrees, "linked")):
            self.skipTest("リンクが作れない")
        result = self.run_clean("linked")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertTrue(os.path.exists(os.path.join(precious, "node_modules", "keep.js")))

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


if __name__ == "__main__":
    unittest.main()
