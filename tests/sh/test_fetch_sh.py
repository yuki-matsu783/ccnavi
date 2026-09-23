"""ccnavi-fetch.sh の受入テスト。使い捨てのワークスペースを組み立てて sh を外から叩く。

見るのは 2 つ。

1. そのツリーがチェックアウトしているブランチを upstream まで ff で進める（承認済みチケットと
   マーカーが届く道。設計 §9.2）
2. ワークツリーの起点になるデフォルトブランチを、チェックアウトされていなくても ff で進める
   （ADR-0060）

ワークスペースは一時ディレクトリに git と bare のリモートで作る。リモートを進めるのは
別に clone した押し手で、ワークスペース側からは「他の機械が push した」ように見える。

読み返すのは終了コード・標準出力と、git に残った ref だけ。

`CCNAVI_SH_DIR` で、写す sh の出どころを差し替えられる。既定はこのツリーの
`.ccnavi/scripts/`（テストしているソースそのもの）。
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest

from tests import ROOT

SHELL = shutil.which("sh") or shutil.which("bash")
GIT = shutil.which("git")
SH_DIR = os.path.join(ROOT, os.environ.get("CCNAVI_SH_DIR", "") or ".ccnavi/scripts")
SCRIPTS = ("ccnavi-fetch.sh", "ccnavi-common.sh")
CONFIG = (
    ("user.email", "t@example.invalid"),
    ("user.name", "t"),
    ("commit.gpgsign", "false"),
)


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def git(cwd, *args, check=True):
    done = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and done.returncode != 0:
        raise AssertionError(f"git {args}: {done.stderr}")
    return done


def posix(path):
    return path.replace(os.sep, "/")


@unittest.skipIf(SHELL is None or GIT is None, "sh か git が無い")
class FetchTest(unittest.TestCase):
    """ワークスペース 1 つと bare のリモート 1 つ。ルートは `main` に居る。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = os.path.join(self._tmp.name, "ws")
        scripts = os.path.join(self.ws, ".ccnavi", "scripts")
        os.makedirs(scripts)
        for name in SCRIPTS:
            shutil.copy(os.path.join(SH_DIR, name), scripts)
        # 承認済みチケットの置き場。第 1 周はこれがあるツリーだけを見る。
        os.makedirs(os.path.join(self.ws, ".ccnavi", "approved", "doing"))
        self.remote = self.repository(self.ws)

    # ---- 道具

    def repository(self, path):
        """git のリポジトリと、その bare のリモートを作る。リモートの綴りを返す。"""
        git(self._tmp.name, "init", "-q", "-b", "main", path)
        for key, value in CONFIG:
            git(path, "config", key, value)
        write(os.path.join(path, ".gitignore"), ".claude/worktrees/\n")
        write(os.path.join(path, "README.md"), "seed\n")
        git(path, "add", "--", ".gitignore", "README.md")
        git(path, "commit", "-q", "-m", "seed")
        remote = os.path.join(self._tmp.name, os.path.basename(path) + ".git")
        git(self._tmp.name, "init", "-q", "--bare", "-b", "main", remote)
        git(path, "remote", "add", "origin", remote)
        git(path, "push", "-q", "-u", "origin", "main")
        return remote

    def advance(self, remote, branch="main"):
        """他の機械の代わり。bare のリモートの <branch> を 1 件進め、その sha を返す。"""
        pusher = os.path.join(self._tmp.name, "push-" + os.path.basename(remote))
        if not os.path.isdir(pusher):
            git(self._tmp.name, "clone", "-q", remote, pusher)
            for key, value in CONFIG:
                git(pusher, "config", key, value)
        git(pusher, "checkout", "-q", branch)
        git(pusher, "pull", "-q", "--ff-only", "origin", branch)
        count = len(os.listdir(pusher))
        write(os.path.join(pusher, f"r{count}.txt"), "remote\n")
        git(pusher, "add", "-A")
        git(pusher, "commit", "-q", "-m", f"remote {count}")
        git(pusher, "push", "-q", "origin", branch)
        return self.sha(pusher, "HEAD")

    def fetch(self, cwd=None, **extra):
        script = posix(os.path.join(self.ws, ".ccnavi", "scripts", "ccnavi-fetch.sh"))
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        env.update(extra)
        return subprocess.run(
            [SHELL, script],
            cwd=cwd or self.ws,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def sha(self, tree, ref):
        done = git(tree, "rev-parse", "--verify", "-q", ref, check=False)
        return done.stdout.strip() if done.returncode == 0 else ""

    def lines(self, done):
        """報せの本文。先頭の見出しは落とす。"""
        out = [line for line in done.stdout.splitlines() if line.strip()]
        return out[1:] if out and out[0].startswith("[ccnavi]") else out

    def leave_main(self, tree=None, branch="claude/x"):
        """ルートを `main` 以外に移す。issue #56 のワークスペースと同じ形。"""
        git(tree or self.ws, "checkout", "-q", "-b", branch)

    # ---- 起点になるデフォルトブランチ

    def test_default_branch_advances_while_root_is_elsewhere(self):
        # ルートが main に居なくても、ワークツリーの起点になる main は新しくなる。
        self.leave_main()
        head = self.advance(self.remote)
        done = self.fetch()
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(head, self.sha(self.ws, "refs/heads/main"))
        self.assertEqual(1, len(self.lines(done)), done.stdout)
        self.assertIn("起点", done.stdout)
        self.assertIn("main", done.stdout)

    def test_default_branch_comes_from_origin_head(self):
        # `origin/HEAD` が指すものが起点。名前が main とは限らない。
        git(self.ws, "checkout", "-q", "-b", "trunk")
        git(self.ws, "push", "-q", "-u", "origin", "trunk")
        git(self.ws, "remote", "set-head", "origin", "trunk")
        self.leave_main()
        before = self.sha(self.ws, "refs/heads/main")
        head = self.advance(self.remote, "trunk")
        self.advance(self.remote, "main")
        done = self.fetch()
        self.assertEqual(head, self.sha(self.ws, "refs/heads/trunk"))
        self.assertEqual(before, self.sha(self.ws, "refs/heads/main"))
        self.assertIn("trunk", done.stdout)

    def test_default_branch_is_created_when_it_is_missing(self):
        # 手元に無いと `worktree add ... main` の起点に指せない。リモートの版で作る。
        self.leave_main()
        head = self.sha(self.ws, "refs/heads/main")
        git(self.ws, "branch", "-q", "-D", "main")
        done = self.fetch()
        self.assertEqual(head, self.sha(self.ws, "refs/heads/main"))
        self.assertEqual(
            "origin/main",
            git(self.ws, "rev-parse", "--abbrev-ref", "main@{u}").stdout.strip(),
        )
        self.assertIn("手元に無かった", done.stdout)

    def test_default_branch_is_left_alone_when_it_diverged(self):
        # 手元にしか無いコミットがあるときは触らない。ff で入らないものは人が合流させる。
        write(os.path.join(self.ws, "local.txt"), "local\n")
        git(self.ws, "add", "-A")
        git(self.ws, "commit", "-q", "-m", "local")
        mine = self.sha(self.ws, "refs/heads/main")
        self.leave_main()
        self.advance(self.remote)
        done = self.fetch()
        self.assertEqual(mine, self.sha(self.ws, "refs/heads/main"))
        self.assertIn("分岐", done.stdout)

    def test_default_branch_is_left_alone_when_its_tree_is_dirty(self):
        # チェックアウトしているツリーに書きかけがあれば触らない（他セッションのもの）。
        tree = os.path.join(self.ws, ".claude", "worktrees", "main-tree")
        self.leave_main()
        git(self.ws, "worktree", "add", "-q", tree, "main")
        before = self.sha(self.ws, "refs/heads/main")
        self.advance(self.remote)
        write(os.path.join(tree, "README.md"), "書きかけ\n")
        done = self.fetch()
        self.assertEqual(before, self.sha(self.ws, "refs/heads/main"))
        self.assertIn("未コミットの変更", done.stdout)

    def test_default_branch_advances_in_the_tree_that_holds_it(self):
        # チェックアウトされているブランチの ref は付け替えず、そのツリーで ff する。
        tree = os.path.join(self.ws, ".claude", "worktrees", "main-tree")
        self.leave_main()
        git(self.ws, "worktree", "add", "-q", tree, "main")
        head = self.advance(self.remote)
        done = self.fetch()
        self.assertEqual(head, self.sha(tree, "HEAD"))
        self.assertEqual("", git(tree, "status", "--porcelain").stdout.strip())
        self.assertIn("起点", done.stdout)

    def test_project_default_branch_advances(self):
        # プロジェクトも同じ。起点はそのプロジェクトのデフォルトブランチ。
        project = os.path.join(self.ws, "projects", "p")
        remote = self.repository(project)
        os.makedirs(os.path.join(project, ".ccnavi", "approved", "doing"))
        self.leave_main(project, "claude/p")
        head = self.advance(remote)
        done = self.fetch()
        self.assertEqual(head, self.sha(project, "refs/heads/main"))
        self.assertIn("p: ", done.stdout)

    # ---- 承認済みチケットとマーカー（第 1 周）

    def test_checked_out_branch_advances_for_approved_tickets(self):
        self.leave_main()
        git(self.ws, "push", "-q", "-u", "origin", "claude/x")
        head = self.advance(self.remote, "claude/x")
        done = self.fetch()
        self.assertEqual(head, self.sha(self.ws, "HEAD"))
        self.assertIn("承認済みチケットとマーカー", done.stdout)

    def test_default_branch_is_not_reported_twice(self):
        # ルートが main に居るなら第 1 周で済む。第 2 周は同じことを言わない。
        head = self.advance(self.remote)
        done = self.fetch()
        self.assertEqual(head, self.sha(self.ws, "refs/heads/main"))
        self.assertEqual(1, len(self.lines(done)), done.stdout)
        self.assertIn("承認済みチケットとマーカー", done.stdout)
        self.assertNotIn("起点", self.lines(done)[0])

    # ---- 届かないとき

    def test_unreachable_origin_is_tried_once(self):
        """git が届かなくても止めない。同じ origin には 1 度しか取りに行かず、1 行だけ言う。"""
        self.leave_main()
        git(self.ws, "branch", "-q", "--set-upstream-to", "origin/main")
        missing = posix(os.path.join(self._tmp.name, "nowhere.git"))
        git(self.ws, "remote", "set-url", "origin", "file://user:secret@" + missing.lstrip("/"))
        done = self.fetch()
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(1, done.stdout.count("取ってこられなかった"), done.stdout)
        self.assertNotIn("secret", done.stdout)

    def test_a_hanging_remote_is_cut_off(self):
        """応答しないリモートは CCNAVI_FETCH_TIMEOUT 秒で切る。hook の上限まで待たない。"""
        helpers = os.path.join(self._tmp.name, "bin")
        helper = write(os.path.join(helpers, "git-remote-slow"), "#!/bin/sh\nsleep 60\n")
        os.chmod(helper, os.stat(helper).st_mode | stat.S_IXUSR)
        git(self.ws, "remote", "set-url", "origin", "slow::x")
        path = os.pathsep.join([helpers, os.environ.get("PATH", "")])
        started = time.monotonic()
        done = self.fetch(PATH=path, CCNAVI_FETCH_TIMEOUT="2")
        self.assertLess(time.monotonic() - started, 20, "見張りが切っていない")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("取ってこられなかった", done.stdout)
        self.assertEqual("", done.stderr.strip())

    # ---- 黙るとき

    def test_quiet_when_nothing_moves(self):
        done = self.fetch()
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("", done.stdout.strip())

    def test_quiet_without_a_remote(self):
        git(self.ws, "remote", "remove", "origin")
        self.leave_main()
        done = self.fetch()
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("", done.stdout.strip())


if __name__ == "__main__":
    unittest.main()
