"""ccnavi-fetch.sh の受入テスト。使い捨てのワークスペースを組み立てて sh を外から呼ぶ。

見るのは 2 つ。

1. そのツリーがチェックアウトしているブランチを upstream まで ff で進める（承認済みチケットと
   マーカーが届く経路）
2. ワークツリーの起点になる統合先ブランチを、チェックアウトされていなくても ff で進める
   （古い起点から枝が伸びないようにする）

ワークスペースは一時ディレクトリに git と bare のリモートで作る。リモートを進めるのは
別に clone した押し手で、ワークスペース側からは「他の機械が push した」ように見える。

読み返すのは終了コード・標準出力と、git に残った ref だけ。

`CCNAVI_SH_DIR` で、コピーする sh の出どころを差し替えられる。既定はこのツリーの
`.ccnavi/scripts/`（テストしているソースそのもの）。
"""

from __future__ import annotations

import http.server
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from tests import ROOT, common_sh, live_sh_pid

SHELL = shutil.which("sh") or shutil.which("bash")
GIT = shutil.which("git")
SH_DIR = os.path.join(ROOT, os.environ.get("CCNAVI_SH_DIR", "") or ".ccnavi/scripts")
SCRIPTS = ("ccnavi-fetch.sh", *common_sh(SH_DIR))
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
        # clone のときに置かれる origin/HEAD（普通の fetch では更新されない）。
        git(path, "remote", "set-head", "origin", "main")
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
        # 親のワークツリーかは実行ファイルに聞く（sh はチケットを読まない）。
        # ソースの実行ファイルを渡す。
        env["CCNAVI_BIN_PATH"] = self.source_exe()
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

    def source_exe(self):
        path = os.path.join(os.path.dirname(self.ws), "source-bin", "ccnavi")
        if not os.path.exists(path):
            write(
                path,
                f'#!/bin/sh\nPYTHONPATH="{ROOT}/src" exec "{sys.executable}" -m ccnavi "$@"\n',
            )
            os.chmod(path, 0o755)
        return path

    def host_default(self, branch):
        """ホストの既定ブランチ（bare のリモートの HEAD）を別のブランチへ変える。"""
        git(
            self._tmp.name, "--git-dir", self.remote, "symbolic-ref", "HEAD", f"refs/heads/{branch}"
        )

    def origin_head(self, repo=None):
        done = git(repo or self.ws, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
        return done.stdout.strip()

    def spy_on_ls_remote(self, fail=False, answer=None, hang=False, fail_fetch=False):
        """`git ls-remote` が打たれた回数を残す git の代役。fail なら ls-remote だけ失敗させる。
        answer があれば、ls-remote の出力をその 1 行（`ref: refs/heads/<名前>\tHEAD`）に差し替える。
        hang なら ls-remote が返事をしない。fail_fetch なら fetch だけ失敗させる（fetch の回数は
        返す記録の隣の `.fetch` に残る）。PATH に足す値と、ls-remote の記録のパスを返す。"""
        bindir = os.path.join(self._tmp.name, "spy-bin")
        log = os.path.join(self._tmp.name, "ls-remote.log")
        fetch_log = log + ".fetch"
        script = '#!/bin/sh\nfor a in "$@"; do\n  if [ "$a" = ls-remote ]; then\n'
        script += f"    echo \"$*\" >>'{posix(log)}'\n"
        if hang:
            script += "    exec sleep 30\n"
        if fail:
            script += "    echo 'fatal: unable to access' >&2; exit 128\n"
        if answer is not None:
            script += f"    printf 'ref: refs/heads/%s\\tHEAD\\n' '{answer}'; exit 0\n"
        script += "  fi\n"
        script += '  if [ "$a" = fetch ]; then\n'
        script += f"    echo \"$*\" >>'{posix(fetch_log)}'\n"
        if fail_fetch:
            script += "    echo 'fatal: could not read from remote repository' >&2; exit 128\n"
        script += "  fi\ndone\n"
        script += f"exec '{posix(GIT)}' \"$@\"\n"
        write(os.path.join(bindir, "git"), script)
        os.chmod(os.path.join(bindir, "git"), 0o755)
        return os.pathsep.join([bindir, os.environ.get("PATH", "")]), log

    def fetch_calls(self, log):
        """spy_on_ls_remote の代役が受けた fetch の行。"""
        try:
            with open(log + ".fetch", encoding="utf-8") as f:
                return f.read().splitlines()
        except FileNotFoundError:
            return []

    def sha(self, tree, ref):
        done = git(tree, "rev-parse", "--verify", "-q", ref, check=False)
        return done.stdout.strip() if done.returncode == 0 else ""

    def lines(self, done):
        """報せの本文。先頭の見出しは除く。"""
        out = [line for line in done.stdout.splitlines() if line.strip()]
        return out[1:] if out and out[0].startswith("[ccnavi]") else out

    def leave_main(self, tree=None, branch="claude/x"):
        """ルートを `main` 以外に移す。issue #56 のワークスペースと同じ形。"""
        git(tree or self.ws, "checkout", "-q", "-b", branch)

    # ---- 起点になる統合先ブランチ

    def test_default_branch_advances_while_root_is_elsewhere(self):
        # ルートが main に居なくても、ワークツリーの起点になる main は新しくなる。
        self.leave_main()
        head = self.advance(self.remote)
        done = self.fetch()
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(head, self.sha(self.ws, "refs/heads/main"))
        self.assertEqual(1, len(self.lines(done)), done.stdout)
        self.assertIn("統合先ブランチ", "\n".join(self.lines(done)))
        self.assertIn("main", done.stdout)

    def test_default_branch_comes_from_origin_head(self):
        # `origin/HEAD` が指すものが起点。名前が main とは限らない。
        git(self.ws, "checkout", "-q", "-b", "trunk")
        git(self.ws, "push", "-q", "-u", "origin", "trunk")
        git(self.ws, "remote", "set-head", "origin", "trunk")
        self.host_default("trunk")
        self.leave_main()
        before = self.sha(self.ws, "refs/heads/main")
        head = self.advance(self.remote, "trunk")
        self.advance(self.remote, "main")
        done = self.fetch()
        self.assertEqual(head, self.sha(self.ws, "refs/heads/trunk"))
        self.assertEqual(before, self.sha(self.ws, "refs/heads/main"))
        self.assertIn("trunk", done.stdout)

    def test_default_branch_is_created_when_it_is_missing(self):
        # 手元に無いと `worktree add ... main` の起点に指せない。origin/main から作る。
        self.leave_main()
        head = self.sha(self.ws, "refs/heads/main")
        git(self.ws, "branch", "-q", "-D", "main")
        done = self.fetch()
        self.assertEqual(head, self.sha(self.ws, "refs/heads/main"))
        self.assertEqual(
            "origin/main",
            git(self.ws, "rev-parse", "--abbrev-ref", "main@{u}").stdout.strip(),
        )
        self.assertIn("ローカルに統合先ブランチ main が無かった", done.stdout)

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

    def test_default_branch_in_a_shallow_clone_is_reported_as_not_matching(self):
        # 浅い clone で履歴が切れると、手元の版が丸ごと「リモートに無いコミット」に見える。
        # 原因は見分けず、リモートの最新と一致しないことと両方の sha を言う。
        # 手元の ref には触らない。
        for _ in range(3):
            self.advance(self.remote)
        git(self.ws, "pull", "-q", "--ff-only", "origin", "main")
        git(self.ws, "update-ref", "refs/heads/main", self.sha(self.ws, "HEAD") + "~2")
        self.leave_main()
        git(self.ws, "fetch", "-q", "--depth", "1", "origin", "main")
        before = self.sha(self.ws, "refs/heads/main")
        unrelated = git(self.ws, "merge-base", "main", "origin/main", check=False)
        self.assertNotEqual(0, unrelated.returncode)
        done = self.fetch()
        self.assertEqual(before, self.sha(self.ws, "refs/heads/main"))
        self.assertIn("一致しない", done.stdout)
        self.assertIn(before[:7], done.stdout)
        self.assertIn(self.sha(self.ws, "origin/main")[:7], done.stdout)

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

    def test_default_branch_does_not_overwrite_an_ignored_file(self):
        # 進める先に ignored と同じパスがあれば、上書きせず進めない。
        # 判定は --no-overwrite-ignore で行う。
        tree = os.path.join(self.ws, ".claude", "worktrees", "main-tree")
        self.leave_main()
        git(self.ws, "worktree", "add", "-q", tree, "main")
        before = self.sha(tree, "HEAD")
        self.advance(self.remote)
        pusher = os.path.join(self._tmp.name, "push-" + os.path.basename(self.remote))
        added = git(pusher, "show", "--name-only", "--format=", "HEAD").stdout.strip()
        write(os.path.join(self.ws, ".git", "info", "exclude"), added + "\n")
        write(os.path.join(tree, added), "ignored の手元のファイル\n")
        done = self.fetch()
        self.assertEqual(before, self.sha(tree, "HEAD"))
        with open(os.path.join(tree, added), encoding="utf-8") as handle:
            self.assertEqual("ignored の手元のファイル\n", handle.read())
        self.assertIn("進められなかった", done.stdout)

    def test_default_branch_advances_in_the_tree_that_holds_it(self):
        # チェックアウトされているブランチの ref は書き換えず、そのツリーで ff する。
        tree = os.path.join(self.ws, ".claude", "worktrees", "main-tree")
        self.leave_main()
        git(self.ws, "worktree", "add", "-q", tree, "main")
        head = self.advance(self.remote)
        done = self.fetch()
        self.assertEqual(head, self.sha(tree, "HEAD"))
        self.assertEqual("", git(tree, "status", "--porcelain").stdout.strip())
        self.assertIn("統合先ブランチ", "\n".join(self.lines(done)))

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
        self.assertIn("親ブランチ", "\n".join(self.lines(done)))

    def test_checked_out_branch_that_does_not_match_is_named_not_merged(self):
        # 進められない理由（分岐か、浅い clone か）は見分けず、一致しないことと両方の sha を言う。
        self.leave_main()
        git(self.ws, "push", "-q", "-u", "origin", "claude/x")
        remote = self.advance(self.remote, "claude/x")
        write(os.path.join(self.ws, "mine.txt"), "mine\n")
        git(self.ws, "add", "--", "mine.txt")
        git(self.ws, "commit", "-q", "-m", "mine")
        mine = self.sha(self.ws, "HEAD")
        done = self.fetch()
        self.assertEqual(mine, self.sha(self.ws, "HEAD"))
        self.assertIn("一致しない", done.stdout)
        self.assertIn(mine[:7], done.stdout)
        self.assertIn(remote[:7], done.stdout)

    def test_default_branch_is_not_reported_twice(self):
        # ルートが main に居るなら第 1 周で済む。第 2 周は同じことを言わない。
        head = self.advance(self.remote)
        done = self.fetch()
        self.assertEqual(head, self.sha(self.ws, "refs/heads/main"))
        self.assertEqual(1, len(self.lines(done)), done.stdout)
        self.assertIn("親ブランチ", "\n".join(self.lines(done)))
        self.assertNotIn("統合先ブランチ", self.lines(done)[0])

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
        """401 を返すリモート。尋ねずに失敗し、認証に失敗したことと、ユーザがすることを言う。"""

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
        self.assertIn("認証に失敗した", done.stdout)
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

    # ---- 取り込み済みの親子のチケット
    # SessionStart は親のワークツリーを早送りする。

    def family(self, name="i0001"):
        """親のワークツリー .claude/worktrees/<name>（親チケットを送ってある）。"""
        self.leave_main()
        tree = os.path.join(self.ws, ".claude", "worktrees", name)
        git(self.ws, "worktree", "add", "-q", tree, "-b", name, "main")
        copy = f".ccnavi/approved/doing/{name}.md"
        write(os.path.join(tree, copy), f"---\nversion: 1\nticket: {name}\n---\n")
        git(tree, "add", "--", copy)
        git(tree, "commit", "-q", "-m", "copy")
        git(tree, "push", "-q", "-u", "origin", name)
        return tree

    def test_a_family_is_fast_forwarded_past_unrelated_work_in_progress(self):
        # 前は未コミットの変更があるだけで進めなかった。
        # 取り込み済みの親子のチケットでは重なりを git に任せる。
        tree = self.family()
        head = self.advance(self.remote, "i0001")
        write(os.path.join(tree, "note.txt"), "書きかけ\n")
        done = self.fetch()
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(head, self.sha(tree, "HEAD"))
        self.assertIn("i0001: 親ブランチ", done.stdout)

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
        self.assertIn("一致しない", done.stdout)
        self.assertIn("取り込みが必要です", done.stdout)
        self.assertIn(mine[:7], done.stdout)
        self.assertIn("ccnavi-sync.sh i0001", done.stdout)

    def test_a_family_under_a_lock_is_skipped(self):
        import socket

        tree = self.family()
        lock = os.path.join(self.ws, "logs", "state", "locks", "self", "i0001")
        os.makedirs(lock)
        now = int(time.time())
        pid = live_sh_pid(self)
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
        # P がリモートに無くても、その origin を失敗したものに数えない（統合先を飛ばさない）。
        self.family()
        pusher = os.path.join(self._tmp.name, "push-" + os.path.basename(self.remote))
        head = self.advance(self.remote)
        git(pusher, "push", "-q", "origin", "--delete", "i0001")
        done = self.fetch()
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("リモートにありません", done.stdout)
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
        self.assertIn("取得しませんでした", done.stdout)

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

    # ---- 手元の origin/HEAD をホストの既定ブランチに合わせる

    def test_a_changed_host_default_moves_origin_head_and_the_base_follows(self):
        # ホストの既定が main から trunk に変わった。手元の origin/HEAD は clone のときのまま。
        # trunk を取り、origin/HEAD を向け直し、1 行出す。第 2 周は trunk で進む。
        git(self.ws, "push", "-q", "origin", "main:trunk")
        self.leave_main()
        self.host_default("trunk")
        head = self.advance(self.remote, "trunk")
        main_before = self.sha(self.ws, "refs/heads/main")
        path, log = self.spy_on_ls_remote()
        done = self.fetch(PATH=path)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("refs/remotes/origin/trunk", self.origin_head())
        said = "ws: 統合先を main から trunk に合わせた（ホストの既定ブランチ）"
        self.assertEqual(1, done.stdout.count(said), done.stdout)
        self.assertEqual(head, self.sha(self.ws, "refs/remotes/origin/trunk"))
        # 第 2 周は新しい名前で進む（手元の trunk は無かったので origin/trunk から作る）。
        self.assertEqual(head, self.sha(self.ws, "refs/heads/trunk"))
        self.assertEqual(main_before, self.sha(self.ws, "refs/heads/main"))
        with open(log, encoding="utf-8") as f:
            self.assertEqual(1, len(f.read().splitlines()))

    def test_a_host_default_branch_not_yet_known_locally_is_caught_up_in_one_session(self):
        # 手元に origin/trunk が無い（set-head は失敗する状態）。1 回のセッションで追いつく。
        git(self._tmp.name, "--git-dir", self.remote, "branch", "trunk", "main")
        self.host_default("trunk")
        self.leave_main()
        self.assertEqual("", self.sha(self.ws, "refs/remotes/origin/trunk"))
        done = self.fetch()
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("refs/remotes/origin/trunk", self.origin_head())
        self.assertNotEqual("", self.sha(self.ws, "refs/remotes/origin/trunk"))
        self.assertNotEqual("", self.sha(self.ws, "refs/heads/trunk"))
        self.assertIn("統合先を main から trunk に合わせた", done.stdout)

    def test_a_project_origin_head_follows_its_own_host(self):
        # ワークスペースの既定は main のまま、プロジェクトのホストだけ trunk に変わる。
        project = os.path.join(self.ws, "projects", "p")
        remote = self.repository(project)
        git(self._tmp.name, "--git-dir", remote, "branch", "trunk", "main")
        git(self._tmp.name, "--git-dir", remote, "symbolic-ref", "HEAD", "refs/heads/trunk")
        self.leave_main()
        self.leave_main(project, "claude/p")
        done = self.fetch()
        self.assertEqual("refs/remotes/origin/main", self.origin_head())
        self.assertEqual("refs/remotes/origin/trunk", self.origin_head(project))
        self.assertIn("p: 統合先を main から trunk に合わせた", done.stdout)
        self.assertNotIn("ws: 統合先を", done.stdout)

    def test_an_unreachable_host_leaves_origin_head_and_says_nothing(self):
        # ls-remote が失敗する（オフライン・時間切れ）。何も変えず何も出さない。
        self.leave_main()
        self.host_default("trunk")
        before = self.origin_head()
        path, log = self.spy_on_ls_remote(fail=True)
        done = self.fetch(PATH=path)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("", done.stdout.strip())
        self.assertEqual(before, self.origin_head())
        self.assertTrue(os.path.exists(log), "ls-remote を打っていない")

    def test_a_failed_fetch_of_the_new_default_does_not_move_origin_head(self):
        # ホストは trunk と答えたが、trunk を取れない（fetch が失敗する）。向け直さない。
        # ls-remote の答えは代役が返し、fetch は代役が失敗させる。向け直す手前の経路を通すため。
        self.leave_main()
        before = self.origin_head()
        path, log = self.spy_on_ls_remote(answer="trunk", fail_fetch=True)
        done = self.fetch(PATH=path)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(before, self.origin_head())
        self.assertNotIn("合わせた", done.stdout)
        self.assertTrue(
            any("refs/heads/trunk" in call for call in self.fetch_calls(log)),
            "trunk を取りに行っていない（向け直す手前の経路を通っていない）",
        )

    def test_a_default_the_host_does_not_have_does_not_move_origin_head(self):
        # ホストは trunk と答えたが、リモートに trunk が無い（本物の fetch が「無い」と返す）。
        # 存在しない ref を指す状態を残さない。
        self.leave_main()
        before = self.origin_head()
        path, _ = self.spy_on_ls_remote(answer="trunk")
        done = self.fetch(PATH=path)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(before, self.origin_head())
        self.assertNotIn("合わせた", done.stdout)

    def test_a_hung_origin_is_not_waited_for_twice(self):
        # 向け直しの ls-remote が返事をしない origin は、第 2 周で同じ origin をもう一度待たない。
        self.leave_main()
        head = self.advance(self.remote)
        path, log = self.spy_on_ls_remote(hang=True)
        started = time.monotonic()
        done = self.fetch(PATH=path, CCNAVI_FETCH_TIMEOUT="2", CCNAVI_FETCH_BUDGET="30")
        elapsed = time.monotonic() - started
        self.assertEqual(0, done.returncode, done.stderr)
        # 向け直しの待ちは 1 回だけ（2 秒）。第 2 周の fetch は打たず、2 回目の待ちが入らない。
        self.assertEqual([], self.fetch_calls(log))
        self.assertLess(elapsed, 8)
        self.assertNotEqual(head, self.sha(self.ws, "refs/heads/main"))
        self.assertEqual(1, done.stdout.count("取ってこられなかった"), done.stdout)

    def test_a_host_default_that_differs_only_in_case_is_the_same_name(self):
        # ホストは Main と答える。手元は main。綴り違いだけなので向け直さず、何も取りに行かない。
        self.leave_main()
        path, log = self.spy_on_ls_remote(answer="Main")
        done = self.fetch(PATH=path)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("refs/remotes/origin/main", self.origin_head())
        self.assertNotIn("合わせた", done.stdout)
        self.assertFalse(any("Main" in call for call in self.fetch_calls(log)))

    def test_a_host_default_starting_with_a_dash_is_not_followed(self):
        # ホストが -x のような名前を返しても、後ろの git の引数に流れないよう向け直さない。
        self.leave_main()
        before = self.origin_head()
        path, log = self.spy_on_ls_remote(answer="-x")
        done = self.fetch(PATH=path)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(before, self.origin_head())
        self.assertNotIn("合わせた", done.stdout)
        self.assertFalse(any("-x" in call for call in self.fetch_calls(log)))

    def test_no_ls_remote_for_an_origin_that_failed_in_the_first_lap(self):
        # 第 1 周で届かなかった origin には、向け直すための ls-remote を打たない。
        self.leave_main()
        git(self.ws, "branch", "-q", "--set-upstream-to", "origin/main")
        missing = posix(os.path.join(self._tmp.name, "nowhere.git"))
        git(self.ws, "remote", "set-url", "origin", "file://" + missing.lstrip("/"))
        path, log = self.spy_on_ls_remote()
        done = self.fetch(PATH=path)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(1, done.stdout.count("取ってこられなかった"), done.stdout)
        self.assertFalse(os.path.exists(log), "第 1 周で失敗した origin に ls-remote を打った")

    def test_a_retarget_failure_is_not_recorded_as_a_failed_origin(self):
        # ls-remote が失敗しても、その origin を「失敗した origin」に数えない。第 2 周は取りに行く。
        self.leave_main()
        head = self.advance(self.remote)
        path, _ = self.spy_on_ls_remote(fail=True)
        done = self.fetch(PATH=path)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(head, self.sha(self.ws, "refs/heads/main"))
        self.assertIn("統合先ブランチ main を", done.stdout)

    def test_a_worktree_is_fast_forwarded_without_any_state(self):
        # 親子ごとの取り込み状態は持たない。HEAD のブランチ名を変換した識別子がフォルダ名と同じ
        # 親のワークツリーは、すべて早送りする。
        tree = self.family()
        before = self.sha(tree, "HEAD")
        head = self.advance(self.remote, "i0001")
        write(os.path.join(tree, "note.txt"), "書きかけ\n")
        git(tree, "add", "--", "note.txt")
        done = self.fetch()
        self.assertNotEqual(before, self.sha(tree, "HEAD"))
        self.assertEqual(head, self.sha(tree, "HEAD"))
        self.assertIn("親ブランチ", "\n".join(self.lines(done)))
        # 書きかけは残る
        self.assertTrue(os.path.exists(os.path.join(tree, "note.txt")))

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
