"""`ccnavi-git.sh integrate` / `integrate --abort` / `integrate-done` の受入テスト。

直接作業（チケットを起こさない）のワークツリーを、マージコミットを作らず staged のまま統合先の
ツリーへ取り込み、確かめた後にコミットして片付ける流れ。使い捨てのワークスペースを毎回組み、
sh を外から呼ぶ。読み返すのは終了コード・出力と、git とファイルシステムに残ったものだけ。

片付け（`ccnavi-clean.sh --worktree`）は実行ファイルの `worktree drop` を呼ぶので、実行ファイルの
代わりにこのツリーのソースを起こす（CCNAVI_BIN_PATH）。

`CCNAVI_SH_DIR` で、写す sh の出どころを差し替えられる。既定はこのツリーの `.ccnavi/scripts/`。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests import ROOT, common_sh

SHELL = shutil.which("sh") or shutil.which("bash")
GIT = shutil.which("git")
SH_DIR = os.path.join(ROOT, os.environ.get("CCNAVI_SH_DIR", "") or ".ccnavi/scripts")
SCRIPTS = ("ccnavi-git.sh", "ccnavi-clean.sh", "ccnavi-clean.js", *common_sh(SH_DIR))


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def git_ok(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True).returncode == 0


def git_supports_merge_tree_write_tree():
    if not GIT:
        return False
    out = subprocess.run(["git", "version"], capture_output=True, text=True).stdout.split()
    try:
        major, minor = (int(x) for x in out[2].split(".")[:2])
    except (IndexError, ValueError):
        return False
    return (major, minor) >= (2, 38)


@unittest.skipUnless(SHELL and GIT, "sh と git が要る")
class IntegrateTest(unittest.TestCase):
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
            f"#!/bin/sh\nPYTHONPATH='{ROOT}/src' exec '{sys.executable}' -m ccnavi \"$@\"\n",
        )
        os.chmod(self.bin, 0o755)
        self.path = None
        git(self.ws, "init", "-q", "-b", "main")
        git(self.ws, "config", "user.email", "t@example.invalid")
        git(self.ws, "config", "user.name", "t")
        write(
            os.path.join(self.ws, ".gitignore"),
            "/.ccnavi/\n/.claude/worktrees/\n/logs/\n/scratchpad/\n*.local\n",
        )
        write(os.path.join(self.ws, "keep.txt"), "1\n2\n3\n")
        write(os.path.join(self.ws, "gone.txt"), "bye\n")
        write(os.path.join(self.ws, "other.txt"), "x\n")
        git(self.ws, "add", ".")
        git(self.ws, "commit", "-q", "-m", "seed")
        git(self.ws, "worktree", "add", "-q", ".claude/worktrees/wt", "-b", "wt", "main")
        self.wt = os.path.join(self.ws, ".claude", "worktrees", "wt")

    # ---- 道具

    def run_git_sh(self, *args, cwd=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        env.pop("CLAUDE_PROJECT_DIR", None)
        env["CCNAVI_BIN_PATH"] = self.bin
        env["CCNAVI_INTEGRATION_BRANCH"] = "main"
        if self.path is not None:
            env["PATH"] = self.path
        script = os.path.join(self.ws, ".ccnavi", "scripts", "ccnavi-git.sh").replace(os.sep, "/")
        return subprocess.run(
            [SHELL, script, *args],
            cwd=cwd or self.ws,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def work_in_branch(self):
        """ワークツリーで、変更・追加・削除を 2 コミットに分けて積む。"""
        write(os.path.join(self.wt, "keep.txt"), "1\n2\nthree\n")
        write(os.path.join(self.wt, "new.txt"), "new\n")
        git(self.wt, "add", ".")
        git(self.wt, "commit", "-q", "-m", "w1")
        git(self.wt, "rm", "-q", "gone.txt")
        git(self.wt, "commit", "-q", "-m", "w2")

    def advance_main(self, name="other.txt", text="y\n"):
        write(os.path.join(self.ws, name), text)
        git(self.ws, "commit", "-q", "-am", "main moves")

    def marker(self):
        return os.path.join(self.ws, ".git", "ccnavi-integrate", "wt", "marker")

    def status(self):
        return git(self.ws, "status", "--porcelain")

    def has_branch(self):
        return git_ok(self.ws, "show-ref", "--verify", "--quiet", "refs/heads/wt")

    def assertOk(self, result, code=0):
        self.assertEqual(code, result.returncode, result.stdout + result.stderr)

    # ---- 正常系

    def test_squash_three_way_then_done_commits_without_merge_commit(self):
        self.work_in_branch()
        self.advance_main()
        before = git(self.ws, "rev-parse", "HEAD").strip()
        r = self.run_git_sh("integrate", "wt")
        self.assertOk(r)
        self.assertIn("ok  git integrate  wt を staged で取り込んだ（3 ファイル", r.stdout)
        self.assertIn("integrate-done wt -m", r.stdout)
        self.assertEqual(
            sorted(self.status().splitlines()), ["A  new.txt", "D  gone.txt", "M  keep.txt"]
        )
        self.assertEqual(before, git(self.ws, "rev-parse", "HEAD").strip())
        self.assertTrue(os.path.isfile(self.marker()))

        again = self.run_git_sh("integrate", "wt")
        self.assertOk(again)
        self.assertIn("取り込み済みで、確認を待っている", again.stdout)

        no_msg = self.run_git_sh("integrate-done", "wt")
        self.assertOk(no_msg, 2)
        self.assertIn("-m <メッセージ>", no_msg.stderr)

        done = self.run_git_sh("integrate-done", "wt", "-m", "feat: 取り込む")
        self.assertOk(done)
        self.assertIn("ok  git integrate-done  wt を取り込んで片付けた", done.stdout)
        self.assertEqual("", self.status())
        parents = git(self.ws, "log", "-1", "--format=%P").split()
        self.assertEqual([before], parents)
        self.assertEqual("feat: 取り込む", git(self.ws, "log", "-1", "--format=%s").strip())
        self.assertEqual("y\n", read(os.path.join(self.ws, "other.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.ws, "gone.txt")))
        self.assertFalse(os.path.exists(self.wt))
        self.assertFalse(self.has_branch())
        self.assertFalse(os.path.exists(self.marker()))

        rerun = self.run_git_sh("integrate-done", "wt")
        self.assertOk(rerun)
        self.assertIn("片付け済み", rerun.stdout)

    def test_manual_commit_then_done_only_cleans(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        git(self.ws, "commit", "-q", "-m", "by hand")
        r = self.run_git_sh("integrate-done", "wt")
        self.assertOk(r)
        self.assertNotIn("コミットした", r.stdout)
        self.assertFalse(os.path.exists(self.wt))
        self.assertFalse(self.has_branch())

    def test_nothing_to_integrate_still_cleans(self):
        r = self.run_git_sh("integrate", "wt")
        self.assertOk(r)
        self.assertIn("取り込む差分は無かった", r.stdout)
        self.assertOk(self.run_git_sh("integrate-done", "wt"))
        self.assertFalse(self.has_branch())

    # ---- 衝突と戻し

    def test_conflict_stops_and_abort_restores(self):
        self.work_in_branch()
        self.advance_main("keep.txt", "1\n2\nTHREE\n")
        head = git(self.ws, "rev-parse", "HEAD").strip()
        r = self.run_git_sh("integrate", "wt")
        self.assertOk(r, 1)
        self.assertIn("衝突した", r.stdout)
        self.assertIn("keep.txt", r.stdout)
        self.assertIn("integrate --abort wt", r.stdout)
        self.assertIn("UU keep.txt", self.status())
        self.assertIn("<<<<<<<", read(os.path.join(self.ws, "keep.txt")))

        done = self.run_git_sh("integrate-done", "wt", "-m", "x")
        self.assertOk(done, 2)
        self.assertIn("衝突で止まって", done.stderr)

        again = self.run_git_sh("integrate", "wt")
        self.assertOk(again, 2)
        self.assertIn("二重には取り込みません", again.stderr)

        ab = self.run_git_sh("integrate", "--abort", "wt")
        self.assertOk(ab)
        self.assertEqual("", self.status())
        self.assertEqual(head, git(self.ws, "rev-parse", "HEAD").strip())
        self.assertEqual("1\n2\nTHREE\n", read(os.path.join(self.ws, "keep.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.ws, ".git", "SQUASH_MSG")))
        self.assertFalse(os.path.exists(self.marker()))
        self.assertTrue(self.has_branch())

    def test_abort_after_success_removes_added_and_restores_deleted(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        self.assertOk(self.run_git_sh("integrate", "--abort", "wt"))
        self.assertEqual("", self.status())
        self.assertFalse(os.path.exists(os.path.join(self.ws, "new.txt")))
        self.assertTrue(os.path.exists(os.path.join(self.ws, "gone.txt")))

    def test_abort_refuses_when_unrelated_paths_are_staged(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        write(os.path.join(self.ws, "someone.txt"), "draft\n")
        git(self.ws, "add", "someone.txt")
        r = self.run_git_sh("integrate", "--abort", "wt")
        self.assertOk(r, 2)
        self.assertIn("someone.txt", r.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.ws, "someone.txt")))
        self.assertTrue(os.path.isfile(self.marker()))

    def test_abort_without_marker_is_refused(self):
        r = self.run_git_sh("integrate", "--abort", "wt")
        self.assertOk(r, 2)
        self.assertIn("記録がありません", r.stderr)

    # ---- 断る

    def test_dirty_integration_tree_is_refused(self):
        self.work_in_branch()
        write(os.path.join(self.ws, "draft.txt"), "someone else\n")
        r = self.run_git_sh("integrate", "wt")
        self.assertOk(r, 2)
        self.assertIn("未コミットの変更", r.stderr)
        self.assertIn("draft.txt", r.stderr)
        self.assertEqual("?? draft.txt\n", self.status())
        self.assertFalse(os.path.exists(self.marker()))

    def test_ignored_files_do_not_count_as_dirty(self):
        self.work_in_branch()
        write(os.path.join(self.ws, "logs", "x.log"), "x\n")
        self.assertOk(self.run_git_sh("integrate", "wt"))

    def test_outside_the_integration_tree_is_refused(self):
        self.work_in_branch()
        r = self.run_git_sh("integrate", "wt", cwd=self.wt)
        self.assertOk(r, 2)
        self.assertIn("統合先（main）をチェックアウトしているツリーでだけ", r.stderr)
        self.assertIn(os.path.realpath(self.ws).replace(os.sep, "/"), r.stderr.replace(os.sep, "/"))
        r = self.run_git_sh("integrate-done", "wt", cwd=self.wt)
        self.assertOk(r, 2)

    def test_ticket_worktree_is_refused(self):
        self.work_in_branch()
        write(
            os.path.join(self.wt, ".ccnavi", "approved", "doing", "wt.md"),
            "---\nticket: wt\n---\n",
        )
        r = self.run_git_sh("integrate", "wt")
        self.assertOk(r, 2)
        self.assertIn("マージリクエスト", r.stderr)
        self.assertEqual("", self.status())

    def test_source_worktree_with_uncommitted_changes_is_refused(self):
        self.work_in_branch()
        write(os.path.join(self.wt, "half.txt"), "wip\n")
        r = self.run_git_sh("integrate", "wt")
        self.assertOk(r, 2)
        self.assertIn("half.txt", r.stderr)

    def test_names_that_are_paths_are_refused(self):
        for name in ("../wt", ".claude/worktrees/wt", "main"):
            with self.subTest(name=name):
                self.assertOk(self.run_git_sh("integrate", name), 2)

    def test_done_without_marker_is_refused(self):
        self.work_in_branch()
        r = self.run_git_sh("integrate-done", "wt", "-m", "x")
        self.assertOk(r, 2)
        self.assertIn("先に", r.stderr)
        self.assertTrue(self.has_branch())
        self.assertTrue(os.path.exists(self.wt))

    def test_done_refuses_changed_staged_content(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        write(os.path.join(self.ws, "new.txt"), "edited\n")
        git(self.ws, "add", "new.txt")
        r = self.run_git_sh("integrate-done", "wt", "-m", "x")
        self.assertOk(r, 2)
        self.assertIn("staged が取り込んだときの中身と違います", r.stderr)
        self.assertTrue(self.has_branch())

    def test_done_refuses_when_branch_moved(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        write(os.path.join(self.wt, "late.txt"), "late\n")
        git(self.wt, "add", ".")
        git(self.wt, "commit", "-q", "-m", "late")
        r = self.run_git_sh("integrate-done", "wt", "-m", "x")
        self.assertOk(r, 2)
        self.assertIn("先頭が取り込んだとき", r.stderr)

    @unittest.skipUnless(git_supports_merge_tree_write_tree(), "git 2.38 以上が要る")
    def test_done_keeps_everything_when_branch_is_not_contained(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        # 取り込みを手で捨てる（記録は残る）。中身は統合先に入っていない。
        git(self.ws, "reset", "-q", "--merge")
        r = self.run_git_sh("integrate-done", "wt")
        self.assertOk(r, 1)
        self.assertIn("入りきっていない", r.stdout)
        self.assertTrue(self.has_branch())
        self.assertTrue(os.path.exists(self.wt))
        self.assertTrue(os.path.isfile(self.marker()))

    @unittest.skipUnless(git_supports_merge_tree_write_tree(), "git 2.38 以上が要る")
    def test_done_keeps_worktree_that_left_the_branch(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        # 取り込んだ後に detached HEAD でコミットを積む。消すと、どの ref からも届かない。
        git(self.wt, "checkout", "-q", "--detach")
        write(os.path.join(self.wt, "precious.txt"), "keep me\n")
        git(self.wt, "add", ".")
        git(self.wt, "commit", "-q", "-m", "precious")
        r = self.run_git_sh("integrate-done", "wt", "-m", "squash wt")
        self.assertOk(r, 1)
        self.assertIn("いまブランチ wt を開いていない", r.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.wt, "precious.txt")))
        self.assertTrue(self.has_branch())

    @unittest.skipUnless(git_supports_merge_tree_write_tree(), "git 2.38 以上が要る")
    def test_done_keeps_detached_commits_even_when_branch_is_gone(self):
        # コミットの無い作業を取り込み、detached HEAD でコミットを積んでから、ブランチを消した。
        self.assertOk(self.run_git_sh("integrate", "wt"))
        git(self.wt, "checkout", "-q", "--detach")
        write(os.path.join(self.wt, "lost.txt"), "do not lose\n")
        git(self.wt, "add", ".")
        git(self.wt, "commit", "-q", "-m", "lost")
        git(self.ws, "branch", "-q", "-d", "wt")
        r = self.run_git_sh("integrate-done", "wt")
        self.assertOk(r, 1)
        self.assertIn("いまブランチ wt を開いていない", r.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.wt, "lost.txt")))

    def test_no_diff_integrate_leaves_no_squash_msg(self):
        self.work_in_branch()
        # 統合先にも同じ変更が先に入っている。squash は差分なしで終わるが、SQUASH_MSG が残りやすい。
        write(os.path.join(self.ws, "keep.txt"), "1\n2\nthree\n")
        write(os.path.join(self.ws, "new.txt"), "new\n")
        git(self.ws, "rm", "-q", "gone.txt")
        git(self.ws, "add", ".")
        git(self.ws, "commit", "-q", "-m", "same change on main")
        r = self.run_git_sh("integrate", "wt")
        self.assertOk(r)
        self.assertIn("差分は無かった", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.ws, ".git", "SQUASH_MSG")))

    def test_abort_refuses_when_staged_content_changed_after_integrate(self):
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        # 他のセッションが、取り込んだパスと同じパスへ staged を足した。戻すと消える。
        with open(os.path.join(self.ws, "new.txt"), "a", encoding="utf-8") as f:
            f.write("other session\n")
        git(self.ws, "add", "new.txt")
        r = self.run_git_sh("integrate", "--abort", "wt")
        self.assertOk(r, 2)
        self.assertIn("変わっています", r.stderr)
        self.assertIn("other session", read(os.path.join(self.ws, "new.txt")))
        self.assertTrue(os.path.isfile(self.marker()))

    # ---- 片付けの途中で止まる・打ち直す

    def test_cleanup_failure_then_retry_is_safe(self):
        self.work_in_branch()
        write(os.path.join(self.wt, "secret.local"), "keep me\n")  # git が無視するファイル
        self.assertOk(self.run_git_sh("integrate", "wt"))
        r = self.run_git_sh("integrate-done", "wt", "-m", "feat: x")
        self.assertOk(r, 1)
        self.assertIn("コミットした", r.stdout)
        self.assertIn("ワークツリー .claude/worktrees/wt を消せなかった", r.stdout)
        self.assertIn("secret.local", r.stdout)
        self.assertIn("残したもの", r.stdout)
        self.assertTrue(self.has_branch())
        self.assertTrue(os.path.isfile(self.marker()))
        commits = git(self.ws, "rev-list", "--count", "HEAD").strip()

        os.remove(os.path.join(self.wt, "secret.local"))
        again = self.run_git_sh("integrate-done", "wt", "-m", "feat: x")
        self.assertOk(again)
        self.assertIn("-m のメッセージは使わなかった", again.stdout)
        self.assertEqual(commits, git(self.ws, "rev-list", "--count", "HEAD").strip())
        self.assertFalse(self.has_branch())
        self.assertFalse(os.path.exists(self.wt))

    def test_old_git_keeps_everything(self):
        real = shutil.which("git")
        shim = os.path.join(self._tmp.name, "oldgit")
        write(
            os.path.join(shim, "git"),
            "#!/bin/sh\n"
            'if [ "$1" = version ]; then echo "git version 2.37.1"; exit 0; fi\n'
            f"exec '{real}' \"$@\"\n",
        )
        os.chmod(os.path.join(shim, "git"), 0o755)
        self.path = shim + os.pathsep + os.environ.get("PATH", "")
        self.work_in_branch()
        self.assertOk(self.run_git_sh("integrate", "wt"))
        r = self.run_git_sh("integrate-done", "wt", "-m", "feat: x")
        self.assertOk(r, 1)
        self.assertIn("2.38", r.stdout)
        self.assertIn("コミットした", r.stdout)
        self.assertTrue(self.has_branch())
        self.assertTrue(os.path.exists(self.wt))
        self.assertTrue(os.path.isfile(self.marker()))


if __name__ == "__main__":
    unittest.main()
