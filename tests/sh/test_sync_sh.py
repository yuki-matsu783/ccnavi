"""ccnavi-sync.sh の受入テスト。

ccnavi-sync.sh は親のブランチを取り込み（早送りか merge）、リモートから消えた親のブランチは
MR がマージ済みかで「閉じた」か「消えた」かを言う。親子ごとの取り込み状態や
統合先のコピーは書かない。親を閉じた後の片付けは、`ready` が書く `retire/<P>.json`（実行ファイルの
`sync retired` が列挙する）が元になる。

使い捨てのワークスペースと bare のリモートを組み、sh を外から呼ぶ。リモートを動かすのは別に clone
した押し手で、ワークスペースからは「他の機械が push した」「ホストがブランチを消した」ように見える。

読み返すのは終了コード・標準出力と、git に残った ref、片付けの記録（logs/state/retire/）の有無だけ。
MR がマージ済みかの問い合わせ（ccnavi-review.sh merged）は、答えを決めた代役の sh に差し替える。

`CCNAVI_SH_DIR` で、写す sh（`SCRIPTS` の 3 本）の出どころを差し替えられる。既定はこのツリーの
`.ccnavi/scripts/`（テストしているソースそのもの）。`.ccnavi/scripts/` に写す前の版
（`wip/design/scripts/` など）を確かめるときに使う（`tests/sh/test_gitwrap.py` と同じ）。
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from tests import ROOT, SRC, common_sh, live_sh_pid

SHELL = shutil.which("sh") or shutil.which("bash")
DASH = shutil.which("dash")
GIT = shutil.which("git")
SH_DIR = os.path.join(ROOT, os.environ.get("CCNAVI_SH_DIR", "") or ".ccnavi/scripts")
SCRIPTS = (
    "ccnavi-sync.sh",
    *common_sh(SH_DIR),
    "ccnavi-git.sh",
)
CONFIG = (
    ("user.email", "t@example.invalid"),
    ("user.name", "t"),
    ("commit.gpgsign", "false"),
)
PARENT = "i0001"
COPY = f".ccnavi/approved/doing/{PARENT}.md"
BASE_SHA = "1" * 40
NO_MR = "mr 欄に MR の番号が無いので、閉じたか（MR がマージ済みか）判断できない"
STARTED_AT = "2026-09-02T00:00:00+09:00"


# 読める承認済みチケットにするための範囲
# （取り込みの後の検査は読めないチケットで親子のチケットを止める）。
ALLOW = 'allow:\n  - match: Write\n    glob: "wip/*"\n'


def copy_text(name=PARENT, base_sha=BASE_SHA, started_at=STARTED_AT, extra="", body="", mr="42"):
    """着手済みの親の承認済みチケット（承認で欄を書かない形。着手の欄はスクリプトが書く）。

    `mr` は親のブランチの MR の番号（空なら欄を書かない）。ccnavi-sync.sh が MR がマージ済みかを
    聞くときの番号で、前は手元の取り込み記録に保存していた。
    """
    head = f"---\nversion: 1\nticket: {name}\n" + (f"mr: {mr}\n" if mr else "") + ALLOW
    fields = f"base_sha: {base_sha}\n" if base_sha else ""
    fields += f"started_at: '{started_at}'\n" if started_at else ""
    return f"{head}{fields}{extra}---\n{body}"


def child_copy_text(name=f"{PARENT}-01-01", parent=PARENT):
    return f"---\nversion: 1\nticket: {name}\nparent: {parent}\nphase: 1\n{ALLOW}---\n"


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


def fields(path):
    with open(path, encoding="utf-8") as f:
        return dict((line.rstrip("\n").split(" ", 1) + [""])[:2] for line in f if line.strip())


def os_name():
    return subprocess.run(["uname", "-s"], capture_output=True, text=True).stdout.strip()


class NoTicketYamlInShTest(unittest.TestCase):
    """sh はチケットの YAML を自分で読まない。閉じたかは `ccnavi-review.sh merged` に、
    親のワークツリーかは `ccnavi tree parent` に聞く（答えの約束は tests/core/test_sh_ask.py）。

    前は sh が frontmatter を awk で読み、Python と同じ値になることを見本で確かめていた。
    読み方を 2 か所に持つと、ずれに気づけない。
    """

    def test_sync_and_common_state_do_not_parse_frontmatter(self):
        for name in ("ccnavi-sync.sh", "ccnavi-common-state.sh"):
            with open(os.path.join(SH_DIR, name), encoding="utf-8") as f:
                code = [line for line in f if not line.lstrip().startswith("#")]
            with self.subTest(name=name):
                for word in (
                    "frontmatter_of",
                    "front_field",
                    "approved_at_of",
                    "^ticket:",
                    "^parent:",
                ):
                    self.assertFalse(any(word in line for line in code), word)


@unittest.skipIf(SHELL is None or GIT is None, "sh か git が無い")
class SyncHarness(unittest.TestCase):
    """ワークスペース 1 つ（main）、bare のリモート、親のワークツリー .claude/worktrees/i0001。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = os.path.join(self._tmp.name, "ws")
        self.scripts = os.path.join(self.ws, ".ccnavi", "scripts")
        os.makedirs(self.scripts)
        for name in SCRIPTS:
            shutil.copy(os.path.join(SH_DIR, name), self.scripts)
        git(self._tmp.name, "init", "-q", "-b", "main", self.ws)
        for key, value in CONFIG:
            git(self.ws, "config", key, value)
        write(os.path.join(self.ws, ".gitignore"), ".claude/worktrees/\nlogs/\n")
        write(os.path.join(self.ws, "README.md"), "seed\n")
        git(self.ws, "add", "--", ".gitignore", "README.md")
        git(self.ws, "commit", "-q", "-m", "seed")
        self.remote = os.path.join(self._tmp.name, "origin.git")
        git(self._tmp.name, "init", "-q", "--bare", "-b", "main", self.remote)
        git(self.ws, "remote", "add", "origin", self.remote)
        git(self.ws, "push", "-q", "-u", "origin", "main")
        git(self.ws, "remote", "set-head", "origin", "main")
        # 親のワークツリー。承認済みの親チケット（mr: 42）をコミットして送ってある。
        self.tree = self.parent_tree(PARENT, push=True)
        self.state = os.path.join(self.ws, "logs", "state")
        # ready が書く片付けの記録の置き場。
        self.retire_dir = os.path.join(self.state, "retire")

    # ---- 道具

    def parent_tree(self, name, push=False, approved=True):
        """親のワークツリー。承認済みの親チケットを置く（`approved=False` なら提案だけ）。"""
        tree = os.path.join(self.ws, ".claude", "worktrees", name)
        git(self.ws, "worktree", "add", "-q", tree, "-b", name, "main")
        if approved:
            rel = f".ccnavi/approved/doing/{name}.md"
            write(os.path.join(tree, rel), copy_text(name))
        else:
            rel = f"wip/proposals/todo/{name}.md"
            write(os.path.join(tree, rel), f"---\nversion: 1\nticket: {name}\nmr: 42\n---\n")
        git(tree, "add", "--", rel)
        git(tree, "commit", "-q", "-m", "copy")
        if push:
            git(tree, "push", "-q", "-u", "origin", name)
        return tree

    def pusher(self):
        """他の機械の代わり。bare のリモートの clone。"""
        path = os.path.join(self._tmp.name, "pusher")
        if not os.path.isdir(path):
            git(self._tmp.name, "clone", "-q", self.remote, path)
            for key, value in CONFIG:
                git(path, "config", key, value)
        git(path, "fetch", "-q", "--prune", "origin")
        return path

    def remote_commit(self, branch, rel, text):
        """リモートの <branch> に 1 件積み、その sha を返す。"""
        path = self.pusher()
        git(path, "checkout", "-q", "-B", branch, f"origin/{branch}")
        write(os.path.join(path, rel), text)
        git(path, "add", "--", rel)
        git(path, "commit", "-q", "-m", f"remote {rel}")
        git(path, "push", "-q", "origin", branch)
        return self.sha(path, "HEAD")

    def delete_remote_branch(self, branch):
        git(self.pusher(), "push", "-q", "origin", "--delete", branch)

    def local_commit(self, rel, text, tree=None):
        tree = tree or self.tree
        write(os.path.join(tree, rel), text)
        git(tree, "add", "--", rel)
        git(tree, "commit", "-q", "-m", f"local {rel}")
        return self.sha(tree, "HEAD")

    def review_says(self, text, code=0):
        """ccnavi-review.sh merged の代役。答えと終了コードを決める。"""
        write(
            os.path.join(self.scripts, "ccnavi-review.sh"),
            f'#!/bin/sh\n[ "$1" = merged ] || exit 2\nprintf "%s\\n" "{text}"\nexit {code}\n'
            if text
            else f"#!/bin/sh\nexit {code}\n",
        )

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        # sh はチケットを読まず実行ファイルに聞く（親のワークツリーか、閉じたか）ので、
        # ソースの実行ファイルを既定で渡す。実行ファイルの無い形を見るテストは自分で上書きする。
        if "CCNAVI_BIN_PATH" not in extra:
            env["CCNAVI_BIN_PATH"] = self.source_exe()
        env.update(extra)
        return env

    def sync(self, *args, shell=None, name="ccnavi-sync.sh", **extra):
        script = os.path.join(self.scripts, name)
        return subprocess.run(
            [shell or SHELL, script, *args],
            cwd=self.ws,
            env=self.env(**extra),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def sha(self, tree, ref):
        done = git(tree, "rev-parse", "--verify", "-q", ref, check=False)
        return done.stdout.strip() if done.returncode == 0 else ""

    def retire_record(self, mr=42, branch=PARENT, name=PARENT, project="", extra=None):
        """ready の後の片付けの記録（`retire/<P>.json`）を置く。パスを返す。"""
        data = {"parent": name, "project": project, "branch": branch, "children": []}
        if mr is not None:
            data["mr"] = mr
        data.update(extra or {})
        path = os.path.join(self.retire_dir, f"{name}.json")
        write(path, json.dumps(data, ensure_ascii=False, indent=1) + "\n")
        return path

    def legacy_record(self, state="gone", mr="42", branch=PARENT, name=PARENT, repo="self"):
        """古い取り込み状態（`sync/<repo>/families/<P>`）を置く。"""
        path = os.path.join(self.state, "sync", repo, "families", name)
        write(
            path,
            f"remote origin\nbranch {branch}\nsha {self.sha(self.tree, 'HEAD')}\n"
            f"fetched_at 1\nstate {state}\nreason \n" + (f"mr {mr}\n" if mr else ""),
        )
        return path

    def git_path(self, tree, name):
        path = git(tree, "rev-parse", "--git-path", name).stdout.strip()
        return path if os.path.isabs(path) else os.path.join(tree, path)

    def lock_dir(self, name=PARENT):
        return os.path.join(self.state, "locks", "self", name)

    def own_lock(self, pid, started, os_part=None, name=PARENT):
        lock = self.lock_dir(name)
        os.makedirs(lock)
        tail = os_name() if os_part is None else os_part
        write(
            os.path.join(lock, "owner"),
            f"{socket.gethostname()} {pid} {started} {pid}-{started} {tail}".rstrip() + "\n",
        )
        return lock

    def dead_pid(self):
        done = subprocess.run(
            [sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True
        )
        return done.stdout.strip()

    def source_exe(self):
        """ソースの実行ファイルを起こすもの。`launcher` とは別の置き場（上書きし合わない）。"""
        path = os.path.join(self._tmp.name, "source-bin", "ccnavi")
        if not os.path.exists(path):
            write(path, f'#!/bin/sh\nPYTHONPATH="{SRC}" exec "{sys.executable}" -m ccnavi "$@"\n')
            os.chmod(path, 0o755)
        return path

    def launcher(self, body=None):
        path = write(
            os.path.join(self._tmp.name, "bin", "ccnavi"),
            body or f'#!/bin/sh\nPYTHONPATH="{SRC}" exec "{sys.executable}" -m ccnavi "$@"\n',
        )
        os.chmod(path, 0o755)
        return path


@unittest.skipIf(SHELL is None or GIT is None, "sh か git が無い")
class SyncTest(SyncHarness):
    # ---- 取り込み（P がリモートにある）

    def test_fast_forward_and_leaves_no_state(self):
        head = self.remote_commit(PARENT, "theirs.txt", "theirs\n")
        done = self.sync(PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(head, self.sha(self.tree, "HEAD"))
        self.assertIn("早送り", done.stdout)
        self.assertIn("統合先: main", done.stdout)
        # 親子ごとの取り込み状態も、統合先のコピーも、もう書かない。
        self.assertFalse(os.path.exists(os.path.join(self.state, "sync")))
        self.assertFalse(os.path.exists(self.retire_dir))
        self.assertFalse(os.path.exists(self.lock_dir()), "ロックが残っている")

    def test_diverged_branches_are_merged(self):
        theirs = self.remote_commit(PARENT, "theirs.txt", "theirs\n")
        mine = self.local_commit("mine.txt", "mine\n")
        done = self.sync(PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("merge で取り込んだ", done.stdout)
        parents = git(self.tree, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()[1:]
        self.assertEqual({theirs, mine}, set(parents))

    def test_a_conflict_is_aborted_and_left_to_a_person(self):
        self.remote_commit(PARENT, COPY, copy_text(body="theirs\n"))
        mine = self.local_commit(COPY, copy_text(body="mine\n"))
        done = self.sync(PARENT)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("衝突", done.stdout)
        self.assertIn(COPY, done.stdout)
        self.assertEqual(mine, self.sha(self.tree, "HEAD"))
        self.assertFalse(os.path.exists(self.git_path(self.tree, "MERGE_HEAD")))
        self.assertEqual("", git(self.tree, "status", "--porcelain").stdout.strip())

    def test_a_merge_in_progress_is_left_alone(self):
        # ユーザ自身の途中の merge を取りやめない。
        other = os.path.join(self._tmp.name, "other")
        git(self.tree, "checkout", "-q", "-b", "side")
        self.local_commit(COPY, copy_text(body="side\n"))
        git(self.tree, "checkout", "-q", PARENT)
        self.local_commit(COPY, copy_text(body="mine\n"))
        git(self.tree, "merge", "side", check=False)
        merge_head = self.git_path(self.tree, "MERGE_HEAD")
        self.assertTrue(os.path.exists(merge_head))
        self.remote_commit(PARENT, "theirs.txt", "theirs\n")
        done = self.sync(PARENT)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("途中の操作（MERGE_HEAD）", done.stdout)
        self.assertTrue(os.path.exists(merge_head), "ユーザの merge を取りやめた")
        self.assertFalse(os.path.exists(other))

    def test_staged_changes_stop_a_merge(self):
        self.remote_commit(PARENT, "theirs.txt", "theirs\n")
        mine = self.local_commit("mine.txt", "mine\n")
        write(os.path.join(self.tree, "staged.txt"), "x\n")
        git(self.tree, "add", "--", "staged.txt")
        done = self.sync(PARENT)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("ステージ済み", done.stdout)
        self.assertEqual(mine, self.sha(self.tree, "HEAD"))

    def test_fast_forward_that_overlaps_work_in_progress_stops(self):
        self.remote_commit(PARENT, "README.md", "theirs\n")
        before = self.sha(self.tree, "HEAD")
        write(os.path.join(self.tree, "README.md"), "書きかけ\n")
        done = self.sync(PARENT)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("書きかけの README.md と重なる", done.stdout)
        self.assertEqual(before, self.sha(self.tree, "HEAD"))
        with open(os.path.join(self.tree, "README.md"), encoding="utf-8") as f:
            self.assertEqual("書きかけ\n", f.read())

    def test_a_refusal_that_is_not_an_overlap_says_why(self):
        # 重なっていないのに「（空）と重なる」と言わない。
        self.remote_commit(PARENT, "theirs.txt", "theirs\n")
        write(os.path.join(self.git_path(self.tree, "index.lock")), "")
        done = self.sync(PARENT)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("index.lock", done.stdout)
        self.assertNotIn("と重なる", done.stdout)

    def test_no_arguments_takes_every_parent_worktree(self):
        other = os.path.join(self.ws, ".claude", "worktrees", "free")
        git(self.ws, "worktree", "add", "-q", other, "-b", "free")
        head = self.remote_commit(PARENT, "theirs.txt", "theirs\n")
        done = self.sync()
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(head, self.sha(self.tree, "HEAD"))
        self.assertNotIn("free", done.stdout)

    def test_a_repeated_argument_is_taken_once(self):
        done = self.sync(PARENT, PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(1, done.stdout.count(f"{PARENT}: リモートと同じ"), done.stdout)
        # 実行ファイルが無ければ、親のワークツリーかを確かめられないので取り込まない。
        done = self.sync(PARENT, CCNAVI_BIN_PATH=os.path.join(self._tmp.name, "nowhere"))
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("実行ファイルに確かめられなかった", done.stdout)

    # ---- 取り込みの後の検査（`--lint` の該当分と判定し直し）

    def test_the_check_reports_an_error_and_a_later_run_clears_it(self):
        launcher = self.launcher()
        done = self.sync(PARENT, CCNAVI_BIN_PATH=launcher)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        # 元ツリーに未コミットで残った子チケット（親のワークツリーの外）。
        # 検査が error を言い、終了コードが 1 になる。状態は何も書かない。
        stray = write(
            os.path.join(self.ws, ".ccnavi", "approved", "doing", f"{PARENT}-01-01.md"),
            child_copy_text(),
        )
        done = self.sync(PARENT, CCNAVI_BIN_PATH=launcher)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("取り込みの後の検査で止める理由がある", done.stdout)
        self.assertIn("ワークツリーの外", done.stdout)
        self.assertIn("再実行してください", done.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.state, "sync")))
        # 理由を片付けて打ち直せば、検査し直して通る。
        os.remove(stray)
        done = self.sync(PARENT, CCNAVI_BIN_PATH=launcher)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertNotIn("検査で止める", done.stdout)

    def test_an_old_executable_that_cannot_check_does_not_block(self):
        # 古い実行ファイル（`sync check` を知らない）は、検査の error と分けて警告だけにする。
        launcher = self.launcher()
        old = write(
            os.path.join(self._tmp.name, "bin", "old-ccnavi"),
            "#!/bin/sh\n"
            'case "$*" in *"sync check"*) echo "ccnavi: unknown command" >&2; exit 2 ;; esac\n'
            f'exec "{launcher}" "$@"\n',
        )
        os.chmod(old, 0o755)
        write(
            os.path.join(self.ws, ".ccnavi", "approved", "doing", f"{PARENT}-01-01.md"),
            child_copy_text(),
        )
        done = self.sync(PARENT, CCNAVI_BIN_PATH=old)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("検査を実行できなかった", done.stdout)

    def test_the_check_shows_warnings_and_errors_from_the_executable(self):
        # sh は実行ファイルの `warn` と `error` の行をそのまま見せ、error があれば 1 で終わる。
        fake = write(
            os.path.join(self._tmp.name, "bin", "fake-check"),
            "#!/bin/sh\n"
            'case "$*" in *"sync check"*)\n'
            '  printf "check 1\\nwarn 層がずれている\\nerror 正とする側が合わない\\n"; '
            "exit 1 ;; esac\n"
            f'exec "{self.launcher()}" "$@"\n',
        )
        os.chmod(fake, 0o755)
        done = self.sync(PARENT, CCNAVI_BIN_PATH=fake)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("注意: 層がずれている", done.stdout)
        self.assertIn("- 正とする側が合わない", done.stdout)
        # warn だけなら通る。
        warn_only = write(
            os.path.join(self._tmp.name, "bin", "warn-check"),
            "#!/bin/sh\n"
            'case "$*" in *"sync check"*)\n'
            '  printf "check 1\\nwarn 層がずれている\\n"; exit 0 ;; esac\n'
            f'exec "{self.launcher()}" "$@"\n',
        )
        os.chmod(warn_only, 0o755)
        done = self.sync(PARENT, CCNAVI_BIN_PATH=warn_only)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("注意: 層がずれている", done.stdout)

    def test_the_check_never_ends_with_3(self):
        # 検査が error を言っても、ロックの再取得も書き込みも無い。終了コードは 1 だけ。
        launcher = self.launcher()
        holder = write(
            os.path.join(self._tmp.name, "bin", "holder"),
            "#!/bin/sh\n"
            'case "$*" in *"sync check"*)\n'
            f'  mkdir -p "{self.state}/locks/self/{PARENT}" && '
            f'printf "elsewhere 1 9999999999 1-1 Other\\n" '
            f'> "{self.state}/locks/self/{PARENT}/owner" ;; esac\n'
            f'exec "{launcher}" "$@"\n',
        )
        os.chmod(holder, 0o755)
        write(
            os.path.join(self.ws, ".ccnavi", "approved", "doing", f"{PARENT}-01-01.md"),
            child_copy_text(),
        )
        done = self.sync(PARENT, CCNAVI_BIN_PATH=holder, CCNAVI_LOCK_WAIT="0")
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertNotIn("まだ止まっていない", done.stdout)

    def test_the_check_is_skipped_for_a_branch_gone_from_the_remote(self):
        # リモートに親のブランチが無い（消えた）親子のチケットは検査しない。
        write(
            os.path.join(self.ws, ".ccnavi", "approved", "doing", f"{PARENT}-01-01.md"),
            child_copy_text(),
        )
        self.review_says("none")
        self.delete_remote_branch(PARENT)
        done = self.sync(PARENT, CCNAVI_BIN_PATH=self.launcher())
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("戻し方 1", done.stdout)
        self.assertNotIn("検査", done.stdout)

    def test_a_single_branch_clone_still_sees_the_parent_branch(self):
        # origin の fetch の refspec が main だけでも、origin/P を進めて取り込む。
        # sh の中の fetch は行き先を書く。
        git(self.ws, "config", "remote.origin.fetch", "+refs/heads/main:refs/remotes/origin/main")
        head = self.remote_commit(PARENT, "theirs.txt", "theirs\n")
        done = self.sync(PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(head, self.sha(self.tree, "HEAD"))

    # ---- P がリモートに無い（マージ後に消えたか、改名・消し間違いか）

    def test_a_family_never_pushed_is_left_as_it_is(self):
        # 一度も送っていない親（origin/P の参照も、承認済みの親チケットも、依頼のマーカーも無い）。
        tree = self.parent_tree("i0002", approved=False)
        done = self.sync("i0002")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("まだ送っていない", done.stdout)
        self.assertTrue(os.path.isdir(tree))

    def test_a_family_with_an_approved_ticket_is_not_taken_for_never_pushed(self):
        # 参照（origin/P）が fetch.prune で消えても、承認済みの親チケットがあれば送った親にする。
        self.review_says("none")
        git(self.ws, "update-ref", "-d", f"refs/remotes/origin/{PARENT}")
        self.delete_remote_branch(PARENT)
        done = self.sync(PARENT)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertNotIn("まだ送っていない", done.stdout)
        self.assertIn("戻し方 1", done.stdout)

    def close_on_main(self, text=None):
        self.remote_commit("main", f".ccnavi/approved/done/{PARENT}.md", text or copy_text())

    def mine(self, text):
        """親のワークツリーの承認済みの親チケットを置き換える。"""
        self.local_commit(COPY, text)
        git(self.tree, "push", "-q", "origin", PARENT)

    def closed_after_delete(self):
        """リモートの親のブランチを消して sync を打つ。MR の答えは「マージされていない」。"""
        self.review_says("none")
        self.delete_remote_branch(PARENT)
        return self.sync(PARENT)

    def assertClosed(self, done):
        """MR がマージ済みと分かって、手元の親を片付けた。"""
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("マージ済み（クローズ済みの親子のチケット）", done.stdout)
        self.assertNotIn("戻し方", done.stdout)

    def assertGone(self, done):
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("戻し方 1", done.stdout)

    def test_a_done_copy_on_the_integration_branch_is_not_consulted(self):
        # 統合先の done/ にある同じ識別子の親チケットは、もう閉じた根拠にしない。
        # 閉じたかは MR がマージ済みかだけで決める（マージされていなければ、消えた親として止める）。
        self.close_on_main()
        done = self.closed_after_delete()
        self.assertGone(done)
        self.assertNotIn("クローズ済みの親子のチケット", done.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.state, "sync")))

    def test_a_parent_missing_in_the_parent_tree_is_not_closed(self):
        # 承認前の提案だけが残る親のワークツリー（この親子のチケットは閉じようがない）。
        os.makedirs(os.path.join(self.tree, "wip", "proposals", "todo"))
        git(self.tree, "mv", "--", COPY, f"wip/proposals/todo/{PARENT}.md")
        git(self.tree, "commit", "-q", "-m", "back to todo")
        git(self.tree, "push", "-q", "origin", PARENT)
        self.close_on_main()
        done = self.closed_after_delete()
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertNotIn("クローズ済みの親子のチケット", done.stdout)

    def test_a_branch_gone_without_a_closed_record_stops_the_family(self):
        self.review_says("none")
        self.delete_remote_branch(PARENT)
        done = self.sync(PARENT)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("決められない", done.stdout)
        self.assertIn("push の関門", done.stdout)
        self.assertIn("戻し方 1", done.stdout)
        self.assertIn(f"git push origin {PARENT}", done.stdout)
        self.assertIn("戻し方 2", done.stdout)
        self.assertIn("worktree remove", done.stdout)
        self.assertIn("retire --abandon i0001", done.stdout)
        self.assertNotIn("--forget", done.stdout)
        # 状態は書かない。ワークツリーが在る間は、何度打っても 1。
        self.assertFalse(os.path.exists(os.path.join(self.state, "sync")))
        self.assertEqual(1, self.sync(PARENT).returncode)
        self.assertEqual(1, self.sync().returncode)

    def test_a_pushed_branch_without_an_mr_number_stops_without_guessing(self):
        # MR の番号（親チケットの mr 欄）が無いので、ブランチから MR を探す推定はせずに止める。
        log = os.path.join(self._tmp.name, "review-args")
        write(os.path.join(self.scripts, "ccnavi-review.sh"), f"#!/bin/sh\necho \"$*\" >>'{log}'\n")
        self.mine(copy_text(mr=""))
        self.delete_remote_branch(PARENT)
        done = self.sync(PARENT)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn(NO_MR, done.stdout)
        self.assertFalse(os.path.exists(log))

    def test_an_unknown_merge_answer_stops_without_calling_it_gone(self):
        # API が失敗した・道具が無い。「消えた」とは言わず、確かめられなかったと言って止まる。
        for text, code in (("unknown", 3), ("", 2), ("", 0), ("merged?", 0)):
            with self.subTest(text=text, code=code):
                self.review_says(text, code)
                if self.sha(self.remote, f"refs/heads/{PARENT}"):
                    self.delete_remote_branch(PARENT)
                done = self.sync(PARENT)
                self.assertEqual(1, done.returncode, done.stdout + done.stderr)
                self.assertIn("確かめられなかった", done.stdout)
                self.assertNotIn("戻し方", done.stdout)

    def test_a_restored_branch_is_taken_in_again(self):
        # 消えた親のブランチをユーザが戻せば、次の sync は普通に取り込む。
        self.review_says("none")
        self.delete_remote_branch(PARENT)
        self.assertEqual(1, self.sync(PARENT).returncode)
        git(self.tree, "push", "-q", "origin", PARENT)
        done = self.sync(PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("リモートと同じ", done.stdout)

    def branches(self):
        return git(self.ws, "branch", "--format=%(refname:short)").stdout.split()

    def test_a_merged_request_cleans_up_without_a_done_copy(self):
        # 統合先には閉じたチケットを残さない（ready が退避して消し、squash でマージする）ので、
        # MR がマージ済みと分かれば閉じたと読み、手元の親のブランチを片付ける。
        # MR の番号は親チケットの mr 欄から引く。
        head = self.sha(self.tree, "HEAD")
        log = self.review_log(f"merged 42 {head}")
        self.delete_remote_branch(PARENT)
        done = self.sync(PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("マージ済み", done.stdout)
        self.assertIn("42", done.stdout)
        self.assertIn(f"ブランチ {PARENT} を削除しました", done.stdout)
        self.assertNotIn("戻し方", done.stdout)
        with open(log, encoding="utf-8") as f:
            self.assertEqual("merged 42", f.read().strip())
        self.assertFalse(os.path.exists(self.tree))
        self.assertEqual(["main"], self.branches())
        self.assertFalse(os.path.exists(self.lock_dir()))

    def test_an_unpushed_commit_keeps_the_branch(self):
        merged_head = self.sha(self.tree, "HEAD")
        self.local_commit("notes.txt", "未 push\n")
        self.review_says(f"merged 42 {merged_head}")
        self.delete_remote_branch(PARENT)
        done = self.sync(PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("未 push のコミットがある", done.stdout)
        self.assertIn(f"branch -D {PARENT}", done.stdout)
        self.assertIn("消さなかったものがある", done.stdout)
        self.assertIn(PARENT, self.branches())

    def test_uncommitted_changes_keep_the_worktree(self):
        self.review_says(f"merged 42 {self.sha(self.tree, 'HEAD')}")
        write(os.path.join(self.tree, "memo.txt"), "書きかけ\n")
        self.delete_remote_branch(PARENT)
        done = self.sync(PARENT)
        self.assertIn("未コミットの変更があるので、何も削除しませんでした", done.stdout)
        self.assertTrue(os.path.isdir(self.tree))
        self.assertIn(PARENT, self.branches())

    def test_without_the_head_of_the_request_nothing_is_deleted(self):
        self.review_says("merged 42")
        self.delete_remote_branch(PARENT)
        done = self.sync(PARENT)
        self.assertIn("先頭の sha をホストから取れなかった", done.stdout)
        self.assertTrue(os.path.isdir(self.tree))
        self.assertIn(PARENT, self.branches())

    # ---- 親のワークツリーを片付けた後（ready の後）。片付けの記録と手元のブランチが残る

    def retire_tree(self):
        """ready の後の状態にする。片付けの記録は、ワークツリーを消す前に置いておく。"""
        git(self.ws, "worktree", "remove", self.tree)

    def test_a_retired_family_is_cleaned_when_its_request_is_merged(self):
        head = self.sha(self.tree, "HEAD")
        record = self.retire_record()
        self.retire_tree()
        self.review_says(f"merged 42 {head} {PARENT}")
        self.delete_remote_branch(PARENT)
        done = self.sync()
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("ワークツリーは片付け済み", done.stdout)
        self.assertIn(f"親のブランチ {PARENT} はリモートから消えた", done.stdout)
        self.assertIn(f"ブランチ {PARENT} を削除しました", done.stdout)
        # 片付けの記録は実行ファイル（sync tidy）が消す。sh は消さない。
        self.assertFalse(os.path.exists(record))
        self.assertEqual(["main"], self.branches())
        # 打ち直しても何も起きない
        again = self.sync()
        self.assertEqual(0, again.returncode, again.stdout + again.stderr)

    def test_a_retired_family_asks_by_the_request_number(self):
        head = self.sha(self.tree, "HEAD")
        self.retire_record(mr=42)
        self.retire_tree()
        log = os.path.join(self._tmp.name, "review-args")
        write(
            os.path.join(self.scripts, "ccnavi-review.sh"),
            f'#!/bin/sh\nprintf "%s\\n" "$*" >>\'{log}\'\nprintf "merged 42 {head} {PARENT}\\n"\n',
        )
        self.sync(PARENT)
        with open(log, encoding="utf-8") as f:
            self.assertEqual("merged 42", f.read().strip())

    def test_a_retired_family_with_an_unmerged_request_is_left_alone(self):
        record = self.retire_record()
        self.retire_tree()
        self.review_says("none")
        done = self.sync()
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("まだマージされていないので片付けない", done.stdout)
        self.assertIn("retire --abandon i0001", done.stdout)
        self.assertTrue(os.path.exists(record))
        self.assertIn(PARENT, self.branches())

    def test_a_retired_family_stops_when_the_merge_cannot_be_checked(self):
        record = self.retire_record()
        self.retire_tree()
        self.review_says("unknown", 3)
        done = self.sync()
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("確かめられなかった", done.stdout)
        self.assertTrue(os.path.exists(record))
        self.assertIn(PARENT, self.branches())

    def test_a_retired_record_without_an_mr_number_is_only_announced(self):
        # mr の無い記録は incomplete と答えられる。一覧の案内だけで、自動では片付けない。
        record = self.retire_record(mr=None)
        self.retire_tree()
        log = self.review_log("none")
        for args in ((), (PARENT,)):
            done = self.sync(*args)
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            self.assertIn("片付けの記録（retire/i0001.json）が使えない", done.stdout)
            self.assertIn("mr", done.stdout)
        self.assertFalse(os.path.exists(log), "番号が無いのにホストへ聞いた")
        self.assertTrue(os.path.exists(record))
        self.assertIn(PARENT, self.branches())

    def test_a_retired_record_of_another_parent_is_not_taken(self):
        # ファイル名と中の parent が合わない記録は数えない（別の親を指させない）。
        path = self.retire_record(name="other")
        os.replace(path, os.path.join(self.retire_dir, f"{PARENT}.json"))
        self.retire_tree()
        log = self.review_log("none")
        done = self.sync()
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertNotIn("ワークツリーは片付け済み", done.stdout)
        self.assertFalse(os.path.exists(log))

    def test_a_retired_record_is_not_listed_while_the_worktree_exists(self):
        # 親のワークツリーが在れば、記録があっても親として取り込む（片付けの列挙には入れない）。
        self.retire_record()
        self.review_says("none")
        done = self.sync()
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertNotIn("ワークツリーは片付け済み", done.stdout)
        self.assertIn(f"{PARENT}: リモートと同じ", done.stdout)

    # ---- リモートにブランチが残っていても、MR がマージ済みなら閉じている

    def review_log(self, answer):
        """答えを決めた ccnavi-review.sh の代役。呼ばれた引数を 1 行ずつ残す。残す先を返す。"""
        log = os.path.join(self._tmp.name, "review-args")
        write(
            os.path.join(self.scripts, "ccnavi-review.sh"),
            f'#!/bin/sh\nprintf "%s\\n" "$*" >>\'{log}\'\nprintf "%s\\n" "{answer}"\n',
        )
        return log

    def test_a_merged_request_whose_branch_is_left_on_the_remote_is_cleaned(self):
        head = self.sha(self.tree, "HEAD")
        self.review_says(f"merged 42 {head} {PARENT}")
        done = self.sync(PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn(f"親のブランチ {PARENT} はリモートに残っているが", done.stdout)
        self.assertIn("片付けた", done.stdout)
        self.assertFalse(os.path.exists(self.tree))
        self.assertEqual(["main"], self.branches())

    def test_an_open_request_of_a_present_family_changes_nothing(self):
        log = self.review_log("none")
        done = self.sync(PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        with open(log, encoding="utf-8") as f:
            self.assertEqual("merged 42", f.read().strip())
        self.assertTrue(os.path.isdir(self.tree))

    def test_a_present_family_without_an_mr_number_is_not_guessed(self):
        self.mine(copy_text(mr=""))
        log = self.review_log(f"merged 7 {self.sha(self.tree, 'HEAD')} {PARENT}")
        done = self.sync(PARENT)
        # 取り込みの後の検査も、mr の無い親チケットを error と言う（終了コード 1）。
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn(NO_MR, done.stdout)
        self.assertIn("mr: が記録されていない", done.stdout)
        self.assertFalse(os.path.exists(log))
        self.assertTrue(os.path.isdir(self.tree))

    def test_a_merged_request_of_another_branch_does_not_clean_this_family(self):
        self.review_says(f"merged 42 {self.sha(self.tree, 'HEAD')} feature/other")
        done = self.sync(PARENT)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("元ブランチ feature/other が親のブランチ", done.stdout)
        self.assertTrue(os.path.isdir(self.tree))

    # ---- ready の後に残った記録（present 以外も）

    def test_a_retired_family_takes_the_branch_from_the_request(self):
        """記録にブランチが無ければ、ブランチ名は MR の元ブランチから取る。"""
        head = self.sha(self.tree, "HEAD")
        self.retire_record(branch="")
        self.retire_tree()
        self.review_says(f"merged 42 {head} {PARENT}")
        done = self.sync()
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        # ブランチはリモートに残っている。消えたとは言わない。
        self.assertIn(f"親のブランチ {PARENT} はリモートに残っている", done.stdout)
        self.assertNotIn("リモートから消え", done.stdout)
        self.assertIn(f"ブランチ {PARENT} を削除しました", done.stdout)
        self.assertEqual(["main"], self.branches())

    def test_a_retired_family_whose_record_names_another_branch_is_not_cleaned(self):
        """記録のブランチと MR の元ブランチが違えば、片付けずに止める。"""
        head = self.sha(self.tree, "HEAD")
        record = self.retire_record(branch="stale-name")
        self.retire_tree()
        self.review_says(f"merged 42 {head} {PARENT}")
        done = self.sync()
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("片付けの記録のブランチ stale-name と違う", done.stdout)
        self.assertTrue(os.path.exists(record))
        self.assertIn(PARENT, self.branches())

    def test_a_retired_family_keeps_the_lock_held_by_the_caller(self):
        """入れ子（外側が持つロック）は、片付けの段で消さない。外すのは持ち主だけ。"""
        record = self.retire_record()
        self.retire_tree()
        self.review_says("none")
        started = str(int(time.time()))
        pid = str(os.getpid())
        lock = self.own_lock(pid, started)
        done = self.sync(CCNAVI_LOCK_HELD=f"self/{PARENT}:{pid}-{started}")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertTrue(os.path.isdir(lock))
        self.assertTrue(os.path.exists(record))

    def archive_locally(self, text):
        write(os.path.join(self.ws, "logs", "archive", "self", "done", f"{PARENT}.md"), text)

    def test_the_local_archive_does_not_close_it_when_merge_is_unknown(self):
        # マージ済みかを確かめられないときは、手元の退避（ready が移した先）の親があっても推定せず、
        # 何も変えずに止める。
        self.review_says("unknown", 3)
        self.archive_locally(copy_text())
        self.delete_remote_branch(PARENT)
        done = self.sync(PARENT)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("確かめられなかった", done.stdout)
        self.assertNotIn("クローズ済みの親子のチケット", done.stdout)

    def archive_the_parent(self):
        """ready の後の状態。親チケットをツリーから消して push し、手元の退避に置く。"""
        rel = f".ccnavi/approved/doing/{PARENT}.md"
        self.archive_locally(copy_text())
        git(self.tree, "rm", "-q", "--", rel)
        git(self.tree, "commit", "-q", "-m", "退避")
        git(self.tree, "push", "-q", "origin", PARENT)

    def test_a_parent_tree_whose_copy_was_archived_is_still_a_parent_tree(self):
        self.archive_the_parent()
        done = self.sync(PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        listed = self.sync()
        self.assertEqual(0, listed.returncode, listed.stdout + listed.stderr)
        self.assertIn(PARENT, listed.stdout)

    def test_an_archived_parent_counts_as_having_been_pushed(self):
        # ready の後（承認済みの親チケットはツリーに無く、退避に在る）で、参照も消えていても、
        # リモートに無い親は「まだ送っていない」とは言わない。
        self.archive_the_parent()
        git(self.ws, "update-ref", "-d", f"refs/remotes/origin/{PARENT}")
        self.review_says("none")
        self.delete_remote_branch(PARENT)
        done = self.sync(PARENT)
        self.assertNotIn("まだ送っていない", done.stdout, done.stdout + done.stderr)

    def test_the_local_archive_does_not_override_an_unmerged_answer(self):
        # マージされていないと分かれば、退避があっても閉じたとは読まない。
        self.review_says("none")
        self.archive_locally(copy_text())
        self.delete_remote_branch(PARENT)
        self.assertGone(self.sync(PARENT))

    def test_branch_names_are_matched_exactly(self):
        # i0001-x があっても i0001 があることにはならない（前方一致で取り違えない）。
        self.review_says("none")
        pusher = self.pusher()
        git(pusher, "push", "-q", "origin", f"origin/{PARENT}:refs/heads/{PARENT}-x")
        self.delete_remote_branch(PARENT)
        self.assertGone(self.sync(PARENT))

    # ---- 廃止した `--forget`（放棄した親の記録は `worktree retire --abandon` で消す）

    def test_forget_is_gone_and_says_what_to_do_instead(self):
        done = self.sync("--forget", PARENT)
        self.assertEqual(2, done.returncode, done.stdout + done.stderr)
        self.assertIn("廃止", done.stderr)
        self.assertIn("retire --abandon", done.stderr)

    def test_an_abandoned_parent_stops_being_listed_when_its_worktree_is_removed(self):
        # 放棄した親（リモートのブランチが消え、MR はマージされていない）は、ワークツリーが在る間は
        # 引数なしでも名指しでも 1 を返す。ワークツリーを片付ければ、列挙されなくなり 1 が止まる。
        self.review_says("none")
        self.delete_remote_branch(PARENT)
        self.assertEqual(1, self.sync(PARENT).returncode)
        self.assertEqual(1, self.sync().returncode)
        git(self.ws, "worktree", "remove", "--force", self.tree)
        done = self.sync()
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertNotIn(PARENT, done.stdout)
        # 名指しすると、ワークツリーが無いと言う（片付けの記録も無い）。
        named = self.sync(PARENT)
        self.assertEqual(1, named.returncode, named.stdout + named.stderr)
        self.assertIn("親のワークツリー（.claude/worktrees/i0001）がありません", named.stdout)

    def test_old_records_of_removed_worktrees_are_moved_to_retire(self):
        # 古い取り込み状態（gone など）のうち、親のワークツリーが無いものは、先頭の
        # `ccnavi sync migrate` が mr と branch を片付けの記録へ移し、古い記録を消す。
        # 状態（gone など）は捨てる。
        legacy = self.legacy_record("gone", mr="42")
        git(self.ws, "worktree", "remove", "--force", self.tree)
        self.review_says("none")
        done = self.sync()
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("古い取り込み記録を、片付けの記録（retire/）へ移した", done.stdout)
        self.assertFalse(os.path.exists(legacy))
        moved = os.path.join(self.retire_dir, f"{PARENT}.json")
        with open(moved, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual((PARENT, 42, PARENT), (data["parent"], data["mr"], data["branch"]))
        # 移した記録は、そのまま片付けの列挙に入る（MR は未マージなので片付けない）。
        self.assertIn("まだマージされていないので片付けない", done.stdout)
        # 打ち直しても移し直さない（冪等）。
        again = self.sync()
        self.assertNotIn("移した", again.stdout)
        self.assertTrue(os.path.exists(moved))

    def test_old_records_of_live_worktrees_are_dropped(self):
        legacy = self.legacy_record("present", mr="42")
        done = self.sync(PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("古い取り込み記録を捨てた", done.stdout)
        self.assertFalse(os.path.exists(legacy))
        self.assertFalse(os.path.exists(os.path.join(self.retire_dir, f"{PARENT}.json")))

    def test_the_old_integration_copy_is_removed(self):
        head = write(
            os.path.join(self.state, "sync", "self", "integration", "head"),
            "remote origin\nbranch main\nsource default\n",
        )
        done = self.sync(PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("古い統合先のコピー", done.stdout)
        self.assertFalse(os.path.exists(head))
        self.assertFalse(os.path.exists(os.path.dirname(head)))

    def test_an_executable_that_does_not_know_migrate_does_not_stop_the_run(self):
        # 古い実行ファイルは `sync migrate` を知らない。何もせずに先へ進む。
        launcher = self.launcher()
        old = write(
            os.path.join(self._tmp.name, "bin", "no-migrate"),
            "#!/bin/sh\n"
            'case "$*" in *"sync migrate"*) echo "ccnavi: unknown command" >&2; exit 2 ;; esac\n'
            f'exec "{launcher}" "$@"\n',
        )
        os.chmod(old, 0o755)
        self.legacy_record("gone")
        done = self.sync(PARENT, CCNAVI_BIN_PATH=old)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertNotIn("移した", done.stdout)

    # ---- 統合先（設定か、無ければホストのデフォルトブランチ）と、
    # 統合先への取り込み（最後に取り込んだ先頭）

    def put_on_main(self, files):
        pusher = self.pusher()
        git(pusher, "checkout", "-q", "main")
        git(pusher, "merge", "-q", "--ff-only", "origin/main")
        for rel, text in files.items():
            write(os.path.join(pusher, rel), text)
        git(pusher, "add", "-A")
        git(pusher, "commit", "-q", "-m", "layers")
        git(pusher, "push", "-q", "origin", "main")
        return pusher

    def test_a_failing_executable_stops_instead_of_using_the_default(self):
        # 実行ファイルが在るのに答えなければ、統合先を何も言わずに既定に戻さない。
        launcher = self.launcher("#!/bin/sh\necho broken >&2\nexit 1\n")
        done = self.sync(PARENT, CCNAVI_BIN_PATH=launcher)
        self.assertEqual(2, done.returncode, done.stdout + done.stderr)
        self.assertIn("返さなかった", done.stderr)

    def test_the_host_default_branch_is_read_from_the_remote(self):
        # 手元の origin/HEAD が無くても、ls-remote --symref でホストの既定を読む。
        # fetch が成功した後に、手元の origin/HEAD をそれに向け直して 1 行出す。
        git(self.pusher(), "push", "-q", "origin", "origin/main:refs/heads/trunk")
        git(self._tmp.name, "--git-dir", self.remote, "symbolic-ref", "HEAD", "refs/heads/trunk")
        git(self.ws, "remote", "set-head", "origin", "--delete")
        done = self.sync(PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("統合先: trunk", done.stdout)
        self.assertIn("統合先を 未設定 から trunk に合わせた（ホストの既定ブランチ）", done.stdout)
        head = git(self.ws, "symbolic-ref", "refs/remotes/origin/HEAD").stdout
        self.assertEqual("refs/remotes/origin/trunk", head.strip())

    def test_a_changed_host_default_branch_moves_the_local_origin_head(self):
        git(self.pusher(), "push", "-q", "origin", "origin/main:refs/heads/trunk")
        git(self._tmp.name, "--git-dir", self.remote, "symbolic-ref", "HEAD", "refs/heads/trunk")
        before = git(self.ws, "symbolic-ref", "refs/remotes/origin/HEAD").stdout.strip()
        self.assertEqual("refs/remotes/origin/main", before)
        done = self.sync(PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("統合先を main から trunk に合わせた（ホストの既定ブランチ）", done.stdout)
        head = git(self.ws, "symbolic-ref", "refs/remotes/origin/HEAD").stdout.strip()
        self.assertEqual("refs/remotes/origin/trunk", head)
        # 合っていれば、2 回目は出さない。
        done = self.sync(PARENT)
        self.assertNotIn("合わせた", done.stdout)

    def test_a_host_default_that_differs_only_in_case_does_not_move_the_origin_head(self):
        # ホストが Main と答え、手元は main。綴り違いだけなので同じ名前として、向け直さない。
        git(self.pusher(), "push", "-q", "origin", "origin/main:refs/heads/Main")
        git(self._tmp.name, "--git-dir", self.remote, "symbolic-ref", "HEAD", "refs/heads/Main")
        done = self.sync(PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertNotIn("合わせた", done.stdout)
        self.assertIn("統合先: main", done.stdout)
        head = git(self.ws, "symbolic-ref", "refs/remotes/origin/HEAD").stdout.strip()
        self.assertEqual("refs/remotes/origin/main", head)
        # 綴り違いの ref は取らない（大文字小文字を区別しない FS で origin/main と衝突する）。
        refs = git(self.ws, "for-each-ref", "--format=%(refname)", "refs/remotes/origin").stdout
        self.assertNotIn("refs/remotes/origin/Main", refs.split())

    def test_a_host_default_starting_with_a_dash_is_not_followed(self):
        # ホストが -x のような名前を返しても使わない（後ろの git の引数でオプションに読まれる）。
        # 手元の origin/HEAD（main）に回る。
        git(self.pusher(), "push", "-q", "origin", "origin/main:refs/heads/-x")
        git(self._tmp.name, "--git-dir", self.remote, "symbolic-ref", "HEAD", "refs/heads/-x")
        done = self.sync(PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("統合先: main", done.stdout)
        self.assertNotIn("合わせた", done.stdout)
        head = git(self.ws, "symbolic-ref", "refs/remotes/origin/HEAD").stdout.strip()
        self.assertEqual("refs/remotes/origin/main", head)

    # ---- 止まる・対象外

    def test_an_unreachable_remote_stops(self):
        git(self.ws, "remote", "set-url", "origin", os.path.join(self._tmp.name, "nowhere.git"))
        done = self.sync(PARENT)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("取ってこられなかった", done.stdout)

    def test_a_project_without_families_does_not_fail_the_run(self):
        # 引数を省いた回で、親子のチケットの無いプロジェクトの ls-remote が失敗しても 1 にしない。
        project = os.path.join(self.ws, "projects", "p")
        git(self._tmp.name, "init", "-q", "-b", "main", project)
        git(project, "remote", "add", "origin", os.path.join(self._tmp.name, "nowhere.git"))
        done = self.sync()
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("p: リモートのブランチの一覧を取ってこられなかった", done.stdout)

    def test_without_origin_nothing_happens(self):
        git(self.ws, "remote", "remove", "origin")
        done = self.sync()
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("origin がありません。", done.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.state, "sync")))

    def test_a_worktree_that_is_not_a_parent_is_refused(self):
        other = os.path.join(self.ws, ".claude", "worktrees", "free")
        git(self.ws, "worktree", "add", "-q", other, "-b", "free")
        done = self.sync("free")
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("親のワークツリーではない", done.stdout)

    def test_a_missing_worktree_says_how_to_cut_it(self):
        done = self.sync("i0002")
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("切り直して", done.stdout)
        self.assertIn("worktree add .claude/worktrees/i0002 <ブランチ>", done.stdout)

    def test_bad_names_are_refused(self):
        for bad in ("../x", "a/b", "-x", "a..b"):
            with self.subTest(bad=bad):
                done = self.sync(bad)
                self.assertEqual(2, done.returncode, done.stdout + done.stderr)

    # ---- ロック（mkdir で取り、ホスト名・pid・開始時刻を書く。古いロックは mv で強制取得する）

    def test_a_held_lock_stops_the_family(self):
        lock = self.own_lock(live_sh_pid(self), int(time.time()))
        head = self.remote_commit(PARENT, "theirs.txt", "theirs\n")
        done = self.sync(PARENT, CCNAVI_LOCK_WAIT="0")
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("ロックを持っている", done.stdout)
        self.assertNotEqual(head, self.sha(self.tree, "HEAD"))
        self.assertTrue(os.path.isdir(lock), "他人のロックを外した")

    def test_a_stale_lock_is_taken_over(self):
        lock = self.own_lock(self.dead_pid(), int(time.time()))
        head = self.remote_commit(PARENT, "theirs.txt", "theirs\n")
        done = self.sync(PARENT, CCNAVI_LOCK_WAIT="0")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(head, self.sha(self.tree, "HEAD"))
        self.assertFalse(os.path.exists(lock), "取ったロックが残っている")
        self.assertFalse(os.path.exists(lock + ".steal"))

    def test_a_dead_pid_on_another_os_is_judged_by_time_only(self):
        # WSL と Git Bash は同じホスト名で pid が通じない。OS が違えば時刻だけで見る。
        lock = self.own_lock(self.dead_pid(), int(time.time()), os_part="OtherOS")
        done = self.sync(PARENT, CCNAVI_LOCK_WAIT="0")
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertTrue(os.path.isdir(lock))

    def test_an_old_lock_with_leading_zeros_is_taken_over(self):
        # 先頭の 0 を 8 進に読んで失敗しない。pid を確かめられない（別の OS）ので時刻で古いと見る。
        lock = self.own_lock("0123", "0000000001", os_part="OtherOS")
        done = self.sync(PARENT, CCNAVI_LOCK_WAIT="0")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertFalse(os.path.exists(lock))

    def test_a_live_owner_is_not_robbed_after_ten_minutes(self):
        # 同じ機械で持ち主が生きていれば、10 分を過ぎても強制取得しない。
        lock = self.own_lock(live_sh_pid(self), int(time.time()) - 3600)
        done = self.sync(PARENT, CCNAVI_LOCK_WAIT="0")
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertTrue(os.path.isdir(lock))

    def test_an_old_lock_of_another_os_is_taken_over_by_time(self):
        lock = self.own_lock(live_sh_pid(self), int(time.time()) - 3600, os_part="OtherOS")
        done = self.sync(PARENT, CCNAVI_LOCK_WAIT="0")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertFalse(os.path.exists(lock))

    def test_another_thief_holds_the_steal_gate(self):
        # 強制取得の操作は 1 つずつ。取得用のロック（<ロック>.steal）が新しければ
        # 強制取得せずに待つ。
        lock = self.own_lock(self.dead_pid(), int(time.time()))
        os.makedirs(lock + ".steal")
        done = self.sync(PARENT, CCNAVI_LOCK_WAIT="0")
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertTrue(os.path.isdir(lock))

    def test_a_forged_nesting_mark_is_not_trusted(self):
        # CCNAVI_LOCK_HELD の持ち主の情報がロックの持ち主と合わなければ入れ子として扱わない。
        lock = self.own_lock(live_sh_pid(self), int(time.time()))
        done = self.sync(PARENT, CCNAVI_LOCK_WAIT="0", CCNAVI_LOCK_HELD=f"self/{PARENT}:1-2")
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("ロックを持っている", done.stdout)
        self.assertTrue(os.path.isdir(lock))

    def test_a_true_nesting_mark_is_trusted_and_left_in_place(self):
        started = int(time.time())
        lock = self.own_lock(live_sh_pid(self), started)
        mark = f"{live_sh_pid(self)}-{started}"
        done = self.sync(PARENT, CCNAVI_LOCK_WAIT="0", CCNAVI_LOCK_HELD=f"self/{PARENT}:{mark}")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertTrue(os.path.isdir(lock), "入れ子で取ったロックを外した")

    @unittest.skipIf(DASH is None, "dash が無い")
    @unittest.skipUnless(hasattr(os, "killpg"), "os.killpg が無い")
    def test_a_terminated_run_releases_its_lock_under_dash(self):
        # dash は EXIT の trap を TERM で走らせない。TERM でもロックを外す。
        write(os.path.join(self.scripts, "ccnavi-review.sh"), "#!/bin/sh\nsleep 30\n")
        self.delete_remote_branch(PARENT)
        script = os.path.join(self.scripts, "ccnavi-sync.sh")
        proc = subprocess.Popen(
            [DASH, script, PARENT],
            cwd=self.ws,
            env=self.env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.addCleanup(self.kill_group, proc)
        lock = self.lock_dir()
        deadline = time.monotonic() + 20
        owner = os.path.join(lock, "owner")
        while not os.path.exists(owner) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertTrue(os.path.isdir(lock), "ロックを取る前に終わった")
        # 端末の Ctrl-C と同じく、プロセスのまとまりへ送る（子も止まり、sh の trap が走る）。
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=20)
        self.assertFalse(os.path.exists(lock), "TERM で終わったのにロックが残っている")
        self.kill_group(proc)

    def kill_group(self, proc):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)


@unittest.skipIf(SHELL is None or GIT is None, "sh か git が無い")
class PlacesAreNotReadTest(SyncHarness):
    """置き場を動かす環境変数は読まない（置き場は固定。A9）。

    `.ccnavi/scripts/` が写す版（i0064-10）になる前は失敗する。写す前の sh は、
    `ccnavi-common.sh` の `ccnavi_state` が `CCNAVI_STATE` を、`ccnavi_parent_tree` が
    `CCNAVI_TICKETS_APPROVED`・`CCNAVI_TICKETS_PROPOSAL` を読み、`ccnavi-sync.sh` の予備が
    `CCNAVI_TICKETS_*`・`CCNAVI_PROJECT_HOME` を、`projects` が `CCNAVI_PROJECTS` を読むため。
    """

    def moved(self):
        """既定と違う綴りの 5 つ。控えはワークスペースの外の絶対パス、ほかは相対。"""
        self.outside = os.path.join(self._tmp.name, "elsewhere-state")
        return {
            "CCNAVI_STATE": self.outside,
            "CCNAVI_TICKETS_APPROVED": "tickets/approved",
            "CCNAVI_TICKETS_PROPOSAL": "tickets/proposals/",
            "CCNAVI_PROJECT_HOME": ".nav",
            "CCNAVI_PROJECTS": "repos",
        }

    def arrange(self):
        """リモートに親の 1 件、統合先に done/ とレイヤー、提案だけの家族、
        家族の無いプロジェクト。"""
        self.head = self.remote_commit(PARENT, "theirs.txt", "theirs\n")
        self.remote_commit("main", ".ccnavi/approved/done/old.md", "old\n")
        self.remote_commit("main", ".ccnavi/config/project/phases.yml", "phases\n")
        # 提案（wip/proposals/todo/）だけがある親。既定の提案の置き場で家族と見つける。
        other = os.path.join(self.ws, ".claude", "worktrees", "i0002")
        git(self.ws, "worktree", "add", "-q", other, "-b", "i0002", "main")
        rel = "wip/proposals/todo/i0002.md"
        write(os.path.join(other, rel), "---\nversion: 1\nticket: i0002\n---\n")
        git(other, "add", "--", rel)
        git(other, "commit", "-q", "-m", "propose")
        git(other, "push", "-q", "-u", "origin", "i0002")
        project = os.path.join(self.ws, "projects", "p")
        git(self._tmp.name, "init", "-q", "-b", "main", project)
        git(project, "remote", "add", "origin", os.path.join(self._tmp.name, "nowhere.git"))

    def assertDefaultPlaces(self, done):
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        # 親のワークツリーを既定の置き場（.ccnavi/approved・wip/proposals）で家族と見つけた。
        self.assertEqual(self.head, self.sha(self.tree, "HEAD"))
        self.assertIn("i0002: リモートと同じ", done.stdout)
        # プロジェクトは既定の projects/ の下で見つける。
        self.assertIn("p: リモートのブランチの一覧を取ってこられなかった", done.stdout)
        # env が指す場所には何も作らない。
        self.assertFalse(os.path.exists(self.outside), "CCNAVI_STATE を読んでいる")
        for rel in ("tickets", ".nav", "repos"):
            self.assertFalse(os.path.exists(os.path.join(self.ws, rel)), rel)

    def test_the_variables_do_not_move_the_places(self):
        """実行ファイルが `sync paths` に答える道。env を入れても既定の置き場で取り込む。"""
        self.arrange()
        done = self.sync(CCNAVI_BIN_PATH=self.launcher(), **self.moved())
        self.assertDefaultPlaces(done)

    def test_the_fallback_without_an_answer_uses_the_defaults(self):
        """実行ファイルが `sync paths` に空で答える道（予備）。sh が既定の綴りを直に使う。

        `sync paths` だけを空で返し、ほか（`write-and-push family` の `branch` の行）
        は実際のものに答えさせる。
        `write-and-push family` まで空だと、sh は親のブランチ名を知らずに取り込みを止める。
        """
        self.arrange()
        body = (
            '#!/bin/sh\nif [ "$3" = sync ] && [ "$4" = paths ]; then exit 0; fi\n'
            f'PYTHONPATH="{SRC}" exec "{sys.executable}" -m ccnavi "$@"\n'
        )
        done = self.sync(CCNAVI_BIN_PATH=self.launcher(body), **self.moved())
        self.assertDefaultPlaces(done)


if __name__ == "__main__":
    unittest.main()
