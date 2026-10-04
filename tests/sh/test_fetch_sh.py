"""ccnavi-fetch.sh の受入テスト。使い捨てのワークスペースを組み立てて sh を外から呼ぶ。

見るのは 2 つ。

1. そのツリーがチェックアウトしているブランチを upstream まで ff で進める（承認済みチケットと
   マーカーが届く経路。設計 9.2）
2. ワークツリーの起点になるデフォルトブランチを、チェックアウトされていなくても ff で進める
   （古い起点から枝が伸びないようにする）

ワークスペースは一時ディレクトリに git と bare のリモートで作る。リモートを進めるのは
別に clone した押し手で、ワークスペース側からは「他の機械が push した」ように見える。

読み返すのは終了コード・標準出力と、git に残った ref だけ。

`CCNAVI_SH_DIR` で、写す sh の出どころを差し替えられる。既定はこのツリーの
`.ccnavi/scripts/`（テストしているソースそのもの）。
"""

from __future__ import annotations

import http.server
import os
import shutil
import stat
import subprocess
import tempfile
import threading
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
        """git のリポジトリと、その bare のリモートを作る。リモートのパスを返す。"""
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
        # 手元にしか無いコミットがあるときは触らない。ff で入らないものはユーザが合流させる。
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
        # 届かないだけで、認証の話ではない。
        self.assertNotIn("認証", done.stdout)

    def test_an_authentication_failure_says_so(self):
        """401 を返すリモート。尋ねずに落ち、認証で落ちたことと、ユーザがすることを言う。"""

        class Unauthorized(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="ccnavi"')
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Unauthorized)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_address[1]}/o/r.git"
        git(self.ws, "remote", "set-url", "origin", url)
        self.leave_main()
        done = self.fetch(LANG="ja_JP.UTF-8", LC_ALL="ja_JP.UTF-8")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(1, done.stdout.count("取ってこられなかった"), done.stdout)
        self.assertIn("認証で落ちた", done.stdout)
        self.assertIn("git fetch origin", done.stdout)

    def test_a_hanging_remote_is_cut_off(self):
        """応答しないリモートは CCNAVI_FETCH_TIMEOUT 秒で切る。hook の上限まで待たない。"""
        helpers = os.path.join(self._tmp.name, "bin")
        helper = write(os.path.join(helpers, "git-remote-slow"), "#!/bin/sh\nsleep 60\n")
        os.chmod(helper, os.stat(helper).st_mode | stat.S_IXUSR)
        git(self.ws, "remote", "set-url", "origin", "slow::x")
        path = os.pathsep.join([helpers, os.environ.get("PATH", "")])
        started = time.monotonic()
        done = self.fetch(PATH=path, CCNAVI_FETCH_TIMEOUT="2")
        self.assertLess(time.monotonic() - started, 20, "タイムアウト監視が切っていない")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("取ってこられなかった", done.stdout)
        self.assertEqual("", done.stderr.strip())

    # ---- 取り込み済みの親子チケット
    # SessionStart は親のワークツリーを早送りするだけで、merge はしない。

    def family(self, name="i0001", record=True):
        """親のワークツリー .claude/worktrees/<name>（親の承認済みチケットを送ってある）
        と、
        親子チケットの同期状態。
        """
        self.leave_main()
        tree = os.path.join(self.ws, ".claude", "worktrees", name)
        git(self.ws, "worktree", "add", "-q", tree, "-b", name, "main")
        copy = f".ccnavi/approved/doing/{name}.md"
        write(os.path.join(tree, copy), f"---\nversion: 1\nticket: {name}\n---\n")
        git(tree, "add", "--", copy)
        git(tree, "commit", "-q", "-m", "copy")
        git(tree, "push", "-q", "-u", "origin", name)
        if record:
            write(
                os.path.join(self.ws, "logs", "state", "sync", "self", "families", name),
                f"remote origin\nbranch {name}\nsha x\nfetched_at 1\nstate present\nreason \n",
            )
        return tree

    def test_a_family_is_fast_forwarded_past_unrelated_work_in_progress(self):
        # 前は未コミットの変更があるだけで進めなかった。親子チケットでは重なりを git に任せる。
        tree = self.family()
        head = self.advance(self.remote, "i0001")
        write(os.path.join(tree, "note.txt"), "書きかけ\n")
        done = self.fetch()
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(head, self.sha(tree, "HEAD"))
        self.assertIn("i0001: 承認済みチケットとマーカー", done.stdout)

    def test_a_family_that_overlaps_work_in_progress_is_not_moved(self):
        tree = self.family()
        before = self.sha(tree, "HEAD")
        pusher = os.path.join(self._tmp.name, "push-" + os.path.basename(self.remote))
        self.advance(self.remote, "i0001")
        # 押し手は r<n>.txt を足す。同じ名前を手元で書きかけにする。
        added = git(pusher, "show", "--name-only", "--format=", "HEAD").stdout.strip()
        write(os.path.join(tree, added), "書きかけ\n")
        done = self.fetch()
        self.assertEqual(before, self.sha(tree, "HEAD"))
        self.assertIn("重なる", done.stdout)
        self.assertIn(added, done.stdout)
        self.assertIn("ccnavi-sync.sh i0001", done.stdout)

    def test_a_diverged_family_is_named_but_not_merged(self):
        tree = self.family()
        self.advance(self.remote, "i0001")
        write(os.path.join(tree, "mine.txt"), "mine\n")
        git(tree, "add", "--", "mine.txt")
        git(tree, "commit", "-q", "-m", "mine")
        mine = self.sha(tree, "HEAD")
        done = self.fetch()
        self.assertEqual(mine, self.sha(tree, "HEAD"))
        self.assertIn("取り込みが要る", done.stdout)
        self.assertIn("ccnavi-sync.sh i0001", done.stdout)

    def test_a_family_under_a_lock_is_skipped(self):
        import socket

        tree = self.family()
        lock = os.path.join(self.ws, "logs", "state", "locks", "self", "i0001")
        os.makedirs(lock)
        now = int(time.time())
        pid = os.getpid()
        write(os.path.join(lock, "owner"), f"{socket.gethostname()} {pid} {now} {pid}-{now}\n")
        before = self.sha(tree, "HEAD")
        self.advance(self.remote, "i0001")
        started = time.monotonic()
        done = self.fetch()
        self.assertLess(time.monotonic() - started, 20, "ロックを待った")
        self.assertEqual(before, self.sha(tree, "HEAD"))
        self.assertIn("他の操作の最中", done.stdout)
        self.assertTrue(os.path.isdir(lock))

    def test_forwarding_stops_after_the_time_budget(self):
        tree = self.family()
        before = self.sha(tree, "HEAD")
        self.advance(self.remote, "i0001")
        done = self.fetch(CCNAVI_FETCH_BUDGET="0")
        self.assertEqual(before, self.sha(tree, "HEAD"))
        self.assertIn("時間の枠", done.stdout)

    def test_a_missing_family_branch_does_not_stop_the_default_branch(self):
        # P がリモートに無くても、その origin を落ちたものに数えない（統合先を飛ばさない）。
        self.family()
        pusher = os.path.join(self._tmp.name, "push-" + os.path.basename(self.remote))
        head = self.advance(self.remote)
        git(pusher, "push", "-q", "origin", "--delete", "i0001")
        done = self.fetch()
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("リモートに無い", done.stdout)
        self.assertIn("ccnavi-sync.sh i0001", done.stdout)
        self.assertEqual(head, self.sha(self.ws, "refs/heads/main"))

    def test_the_budget_is_checked_before_fetching(self):
        # 枠を過ぎたら fetch ごと飛ばして名指しする。起点も進めない。
        tree = self.family()
        before = self.sha(tree, "HEAD")
        main_before = self.sha(self.ws, "refs/heads/main")
        self.advance(self.remote, "i0001")
        self.advance(self.remote)
        started = time.monotonic()
        done = self.fetch(CCNAVI_FETCH_BUDGET="0")
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(before, self.sha(tree, "HEAD"))
        self.assertEqual(main_before, self.sha(self.ws, "refs/heads/main"))
        self.assertIn("時間の枠", done.stdout)
        self.assertIn("取りに行かなかった", done.stdout)

    def test_a_refusal_that_is_not_an_overlap_says_why(self):
        # 重なっていないのに「重なる」と言わない。
        tree = self.family()
        before = self.sha(tree, "HEAD")
        self.advance(self.remote, "i0001")
        lock = git(tree, "rev-parse", "--git-path", "index.lock").stdout.strip()
        write(lock if os.path.isabs(lock) else os.path.join(tree, lock), "")
        done = self.fetch()
        self.assertEqual(before, self.sha(tree, "HEAD"))
        self.assertIn("index.lock", done.stdout)
        self.assertNotIn("と重なる", done.stdout)

    def test_a_family_in_a_single_branch_clone_is_forwarded(self):
        # origin の fetch の refspec が main だけでも origin/P を進める（fetch に行き先を書く）。
        tree = self.family()
        git(self.ws, "config", "remote.origin.fetch", "+refs/heads/main:refs/remotes/origin/main")
        head = self.advance(self.remote, "i0001")
        self.fetch()
        self.assertEqual(head, self.sha(tree, "HEAD"))

    def test_the_worktree_base_follows_the_integration_branch(self):
        # 2 周目（ワークツリーの起点）は統合先に合わせる。
        git(self.ws, "push", "-q", "origin", "main:develop")
        git(self.ws, "branch", "-q", "develop", "main")
        self.leave_main()
        head = self.advance(self.remote, "develop")
        main_before = self.sha(self.ws, "refs/heads/main")
        self.advance(self.remote)
        done = self.fetch(CCNAVI_INTEGRATION_BRANCH="develop")
        self.assertEqual(head, self.sha(self.ws, "refs/heads/develop"))
        self.assertEqual(main_before, self.sha(self.ws, "refs/heads/main"))
        self.assertIn("develop", done.stdout)

    def test_the_worktree_base_follows_the_name_ccnavi_sync_recorded(self):
        git(self.ws, "push", "-q", "origin", "main:develop")
        git(self.ws, "branch", "-q", "develop", "main")
        write(
            os.path.join(self.ws, "logs", "state", "sync", "self", "integration", "head"),
            "remote origin\nbranch develop\nsource settings.local.json\nsha x\nfetched_at 1\n",
        )
        self.leave_main()
        head = self.advance(self.remote, "develop")
        self.fetch()
        self.assertEqual(head, self.sha(self.ws, "refs/heads/develop"))

    def test_a_missing_integration_branch_is_named(self):
        self.leave_main()
        done = self.fetch(CCNAVI_INTEGRATION_BRANCH="nope")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("nope（統合先）がリモートに無い", done.stdout)

    def test_a_worktree_without_a_record_keeps_the_old_rule(self):
        # 同期状態の無い（取り込み済みでない）親子チケットは今までどおり。書きかけがあれば進めない。
        tree = self.family(record=False)
        before = self.sha(tree, "HEAD")
        self.advance(self.remote, "i0001")
        write(os.path.join(tree, "note.txt"), "書きかけ\n")
        git(tree, "add", "--", "note.txt")
        done = self.fetch()
        self.assertEqual(before, self.sha(tree, "HEAD"))
        self.assertIn("未コミットの変更", done.stdout)

    # ---- 何も出さないとき

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
