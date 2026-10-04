"""ccnavi-sync.sh の受入テスト。

ccnavi-sync.sh は親のブランチを取り込み（早送りか merge）、リモートから消えた親のブランチは
統合先の done/ で閉じたかを確かめてから、親子のチケットの取り込み状態に書く。

使い捨てのワークスペースと bare のリモートを組み、sh を外から呼ぶ。リモートを動かすのは別に clone
した押し手で、ワークスペースからは「他の機械が push した」「ホストがブランチを消した」ように見える。

読み返すのは終了コード・標準出力と、git に残った ref、
取り込み状態（logs/state/sync/...）の中身だけ。
MR がマージ済みかの問い合わせ（ccnavi-review.sh merged）は、答えを決めた代役の sh に差し替える。

`CCNAVI_SH_DIR` で、写す sh（`SCRIPTS` の 3 本）の出どころを差し替えられる。既定はこのツリーの
`.ccnavi/scripts/`（テストしているソースそのもの）。`.ccnavi/scripts/` に写す前の版
（`wip/design/scripts/` など）を確かめるときに使う（`tests/sh/test_gitwrap.py` と同じ）。
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from tests import ROOT, SRC, common_sh

SHELL = shutil.which("sh") or shutil.which("bash")
DASH = shutil.which("dash")
GIT = shutil.which("git")
SH_DIR = os.path.join(ROOT, os.environ.get("CCNAVI_SH_DIR", "") or ".ccnavi/scripts")
SCRIPTS = ("ccnavi-sync.sh", *common_sh(SH_DIR), "ccnavi-git.sh")
CONFIG = (
    ("user.email", "t@example.invalid"),
    ("user.name", "t"),
    ("commit.gpgsign", "false"),
)
PARENT = "i0001"
COPY = f".ccnavi/approved/doing/{PARENT}.md"
APPROVED_AT = "2026-09-01T00:00:00+0900"
BASE_SHA = "1" * 40
STARTED_AT = "2026-09-02T00:00:00+09:00"


# 読める承認済みチケットにするための範囲
# （取り込みの後の検査は読めないチケットで親子のチケットを止める）。
ALLOW = 'allow:\n  - match: Write\n    glob: "wip/*"\n'


def copy_text(name=PARENT, base_sha=BASE_SHA, started_at=STARTED_AT, extra="", body=""):
    """着手済みの親の承認済みチケット（承認で欄を書かない形。着手の欄はスクリプトが書く）。"""
    head = f"---\nversion: 1\nticket: {name}\n{ALLOW}"
    fields = f"base_sha: {base_sha}\n" if base_sha else ""
    fields += f"started_at: '{started_at}'\n" if started_at else ""
    return f"{head}{fields}{extra}---\n{body}"


def old_copy_text(name=PARENT, approved_at=APPROVED_AT, body=""):
    """承認で欄を書いていた頃の、未着手の親の承認済みチケット（ブロックの形の ccnavi_approved）。"""
    head = f"---\nversion: 1\nticket: {name}\n{ALLOW}"
    return f"{head}ccnavi_approved:\n  approved_at: {approved_at}\n---\n{body}"


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


@unittest.skipIf(SHELL is None, "sh が無い")
class FrontFieldTest(unittest.TestCase):
    """sh の `front_field` が、Python の `syncstate._text`（YAML で読んだ値）と同じ値を出す。

    `null`・`~`・引用符の外の注記を sh だけが値として読むと、閉じたかの照合が Python と食い違う。
    """

    VALUES = (
        "abc",
        '"abc # x"',
        "'a b'",
        "abc # c",
        "null",
        "Null",
        "NULL",
        "~",
        "",
        '""',
        "#x",
        "a#b",
        '"q" # c',
        "' sp '",
    )

    def sh_value(self, line):
        with open(os.path.join(SH_DIR, "ccnavi-sync.sh"), encoding="utf-8") as f:
            text = f.read()
        start = text.index("front_field() {")
        body = text[start : text.index("\n}\n", start) + 3]
        script = body + 'printf "%s\\n" "$1" | front_field base_sha\n'
        done = subprocess.run(
            [SHELL, "-c", script, "sh", line], capture_output=True, text=True, encoding="utf-8"
        )
        return done.stdout.rstrip("\n")

    def test_the_same_values_as_python(self):
        from ccnavi.tickets import syncstate
        from ccnavi.tickets import ticket as ticket_mod

        for value in self.VALUES:
            with self.subTest(value=value):
                front, _, _ = ticket_mod._frontmatter(f"---\nticket: x\nbase_sha: {value}\n---\n")
                expected = syncstate._text(front.get("base_sha"))
                self.assertEqual(self.sh_value(f"base_sha: {value}"), expected)


@unittest.skipIf(SHELL is None, "sh が無い")
class ApprovedAtOfTest(unittest.TestCase):
    """sh の `approved_at_of` が、Python の `syncstate._copy_fields` と同じ範囲
    （frontmatter の `ccnavi_approved` の中）だけから承認の時刻を拾う。

    本文や block scalar（`rationale: |` の下の行）に同じ語を書いた行を拾うと、古い形どうしの
    照合で別の値を比べる。
    """

    FRONTS = (
        "ccnavi_approved: {approved_at: '2026-01-01T00:00:00+09:00', source_tree: main}",
        "ccnavi_approved:\n  approved_at: 'X1'\n  source_tree: m",
        'ccnavi_approved:\n\n  source_tree: m\n  approved_at: "X2"',
        "rationale: |\n  approved_at: BAD\nccnavi_approved: {source_tree: m}",
        "rationale: |\n  approved_at: BAD\nccnavi_approved:\n  approved_at: 'X3'",
        "ccnavi_approved: |\n  approved_at: BAD",
        "ccnavi_approved:\n  nested:\n    approved_at: BAD\n  approved_at: 'X4'\ntitle: t",
        "ccnavi_approved: {source_tree: m,\n  approved_at: 'X5'}",
        "title: 'approved_at: BAD'",
        "note:\n  approved_at: BAD",
    )

    def sh_value(self, front):
        with open(os.path.join(SH_DIR, "ccnavi-sync.sh"), encoding="utf-8") as f:
            text = f.read()
        script = ""
        for name in ("frontmatter_of", "approved_at_of"):
            start = text.index(f"{name}() {{")
            script += text[start : text.index("\n}\n", start) + 3]
        script += 'printf "%s\\n" "$1" | frontmatter_of | approved_at_of\n'
        document = f"---\nticket: x\n{front}\n---\napproved_at: BODY\n"
        done = subprocess.run(
            [SHELL, "-c", script, "sh", document], capture_output=True, text=True, encoding="utf-8"
        )
        return done.stdout.rstrip("\n")

    def test_the_same_range_as_python(self):
        from ccnavi.tickets import syncstate

        for front in self.FRONTS:
            with self.subTest(front=front):
                expected = syncstate._copy_fields(f"---\nticket: x\n{front}\n---\n").approved_at
                self.assertEqual(self.sh_value(front), expected)


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
        # 親のワークツリー。承認済みの親チケットをコミットして送ってある。
        self.tree = self.parent_tree(PARENT, push=True)
        self.state = os.path.join(self.ws, "logs", "state")
        self.record = os.path.join(self.state, "sync", "self", "families", PARENT)
        self.mirror = os.path.join(self.state, "sync", "self", "integration")

    # ---- 道具

    def parent_tree(self, name, push=False):
        tree = os.path.join(self.ws, ".claude", "worktrees", name)
        git(self.ws, "worktree", "add", "-q", tree, "-b", name, "main")
        rel = f".ccnavi/approved/doing/{name}.md"
        write(os.path.join(tree, rel), copy_text(name))
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
        env.update({"CCNAVI_SYNC_RETRIES": "0", "CCNAVI_SYNC_RETRY_WAIT": "0"})
        env.update(extra)
        return env

    def sync(self, *args, shell=None, **extra):
        script = os.path.join(self.scripts, "ccnavi-sync.sh")
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

    def keep_record(self, state="present"):
        """前に取り込んだ（取り込み状態がある）親子のチケットにする。"""
        write(
            self.record,
            f"remote origin\nbranch {PARENT}\nsha {self.sha(self.tree, 'HEAD')}\n"
            f"fetched_at 1\nstate {state}\nreason \n",
        )

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

    def test_fast_forward_and_records(self):
        head = self.remote_commit(PARENT, ".ccnavi/approved/doing/i0001-01-01.md", "child\n")
        done = self.sync(PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(head, self.sha(self.tree, "HEAD"))
        self.assertIn("早送り", done.stdout)
        self.assertIn("統合先: main（ホストのデフォルトブランチ）", done.stdout)
        record = fields(self.record)
        self.assertEqual("present", record["state"])
        self.assertEqual(head, record["sha"])
        self.assertEqual("origin", record["remote"])
        self.assertTrue(record["fetched_at"].isdigit())
        head_of = fields(os.path.join(self.mirror, "head"))
        self.assertEqual("main", head_of["branch"])
        self.assertEqual("default", head_of["source"])
        self.assertEqual(self.sha(self.ws, "refs/remotes/origin/main"), head_of["sha"])
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
        self.assertTrue(os.path.exists(self.record))
        self.assertNotIn("free", done.stdout)

    def test_a_repeated_argument_is_taken_once(self):
        done = self.sync(PARENT, PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(1, done.stdout.count(f"{PARENT}: リモートと同じ"), done.stdout)
        # 取り込みの後の検査も 1 度だけ（ここは実行ファイルが無いので、しなかったと 1 度言う）。
        self.assertEqual(1, done.stdout.count(f"{PARENT}: 実行ファイルが無い"), done.stdout)

    # ---- 取り込みの後の検査（`--lint` の該当分と判定し直し）

    def test_the_check_blocks_the_family_and_a_later_run_clears_it(self):
        launcher = self.launcher()
        done = self.sync(PARENT, CCNAVI_BIN_PATH=launcher)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual("present", fields(self.record)["state"])
        # 元ツリーに未コミットで残った子チケット（親のワークツリーの外）。
        # 取り込み済みの親子のチケットでは信頼しないので、検査が親子のチケットを止める。
        stray = write(
            os.path.join(self.ws, ".ccnavi", "approved", "doing", f"{PARENT}-01-01.md"),
            child_copy_text(),
        )
        done = self.sync(PARENT, CCNAVI_BIN_PATH=launcher)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        record = fields(self.record)
        self.assertEqual("blocked", record["state"])
        self.assertIn("ワークツリーの外", record["reason"])
        self.assertIn("blocked にした", done.stdout)
        self.assertIn("打ち直して", done.stdout)
        # 理由を片付けて打ち直せば、検査し直して present に戻る。
        os.remove(stray)
        done = self.sync(PARENT, CCNAVI_BIN_PATH=launcher)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        record = fields(self.record)
        self.assertEqual(("present", ""), (record["state"], record["reason"]))

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
        self.assertEqual("present", fields(self.record)["state"])

    def test_the_check_does_not_overwrite_a_record_changed_meanwhile(self):
        # 検査の間に並行する sync が取り込み状態を gone にしたら、blocked で上書きしない。
        launcher = self.launcher()
        racer = write(
            os.path.join(self._tmp.name, "bin", "racer"),
            "#!/bin/sh\n"
            'case "$*" in *"sync check"*)\n'
            f'  sed "s/^state .*/state gone/" "{self.record}" > "{self.record}.x" && '
            f'mv "{self.record}.x" "{self.record}" ;; esac\n'
            f'exec "{launcher}" "$@"\n',
        )
        os.chmod(racer, 0o755)
        write(
            os.path.join(self.ws, ".ccnavi", "approved", "doing", f"{PARENT}-01-01.md"),
            child_copy_text(),
        )
        done = self.sync(PARENT, CCNAVI_BIN_PATH=racer)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("変わった。書き換えない", done.stdout)
        self.assertEqual("gone", fields(self.record)["state"])

    def test_the_check_that_cannot_write_the_record_ends_with_3(self):
        # 止める理由があるのにロックが取れない（他の操作が持ち続ける）なら、3 回試して 3 で終わる。
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
        self.assertEqual(3, done.returncode, done.stdout + done.stderr)
        self.assertIn("まだ止まっていない", done.stdout)
        self.assertEqual("present", fields(self.record)["state"])

    def test_the_check_is_skipped_for_a_closed_family(self):
        # 閉じた親子のチケット（取り込み状態が closed）は検査しない（状態の操作が無い）。
        self.keep_record()
        write(
            os.path.join(self.ws, ".ccnavi", "approved", "doing", f"{PARENT}-01-01.md"),
            child_copy_text(),
        )
        self.remote_commit("main", f".ccnavi/approved/done/{PARENT}.md", copy_text())
        self.delete_remote_branch(PARENT)
        done = self.sync(PARENT, CCNAVI_BIN_PATH=self.launcher())
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual("closed", fields(self.record)["state"])
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
        tree = self.parent_tree("i0002")
        done = self.sync("i0002")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("まだ送っていない", done.stdout)
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.record), "i0002")))
        self.assertTrue(os.path.isdir(tree))

    def close_on_main(self, text=None):
        self.remote_commit("main", f".ccnavi/approved/done/{PARENT}.md", text or copy_text())

    def mine(self, text):
        """親のワークツリーの承認済みの親チケットを置き換える。"""
        self.local_commit(COPY, text)
        git(self.tree, "push", "-q", "origin", PARENT)

    def closed_after_delete(self):
        self.review_says("none")
        self.keep_record()
        self.delete_remote_branch(PARENT)
        done = self.sync(PARENT)
        return fields(self.record)["state"], done

    def test_a_merged_and_deleted_branch_is_closed(self):
        self.keep_record()
        self.close_on_main()
        self.delete_remote_branch(PARENT)
        done = self.sync(PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("閉じた親子のチケット", done.stdout)
        self.assertNotIn("戻し方", done.stdout)
        self.assertEqual("closed", fields(self.record)["state"])

    def test_a_closed_family_without_a_record_is_closed(self):
        # 取り込み状態が無くても、統合先で閉じていれば閉じた親子のチケット。
        self.close_on_main()
        self.delete_remote_branch(PARENT)
        done = self.sync(PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("閉じた親子のチケット", done.stdout)
        self.assertNotIn("まだ送っていない", done.stdout)
        self.assertEqual("closed", fields(self.record)["state"])

    def test_an_old_done_copy_of_the_same_id_is_not_this_family(self):
        # 同じ識別子の古い親チケット（着手の基準点が違う）は、
        # 今の親子のチケットが閉じた記録ではない。後ろの欄（started_at）が同じでも見ない。
        self.close_on_main(copy_text(base_sha="2" * 40))
        state, done = self.closed_after_delete()
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertEqual("gone", state)

    def test_started_at_is_compared_when_base_sha_is_on_one_side_only(self):
        self.mine(copy_text(base_sha=""))
        self.close_on_main(copy_text())
        self.assertEqual("closed", self.closed_after_delete()[0])

    def test_a_parent_cancelled_before_start_is_matched_by_cancelled_at(self):
        # 未着手のまま取り消した親は cancelled_at で照合する（永久に止まらない）。
        unstarted = copy_text(base_sha="", started_at="", extra="cancelled_at: '2026-09-03'\n")
        self.mine(unstarted)
        self.close_on_main(unstarted)
        self.assertEqual("closed", self.closed_after_delete()[0])

    def test_nothing_to_compare_is_not_closed(self):
        # どの欄でも照合できない（空どうし、空文字）なら閉じていない側に倒し、止めて戻し方を出す。
        bare = copy_text(base_sha="''", started_at="")
        self.mine(bare)
        self.close_on_main(bare)
        state, done = self.closed_after_delete()
        self.assertEqual("gone", state)
        self.assertIn("戻し方", done.stdout)

    def test_fields_in_the_body_are_not_read(self):
        # 欄は frontmatter の行頭だけから拾う。本文の同じ語の行で別の値を拾わない。
        self.mine(copy_text(base_sha="", body="base_sha: aaaa\n"))
        self.close_on_main(copy_text(base_sha="", body="base_sha: bbbb\n"))
        self.assertEqual("closed", self.closed_after_delete()[0])

    def test_an_approval_time_in_the_body_is_not_read(self):
        # 承認の時刻は frontmatter の ccnavi_approved の中だけから拾う。本文の同じ語の行で
        # 閉じたと読まない（Python と同じく、照合できる欄が無いので閉じていない）。
        bare = copy_text(base_sha="", started_at="", body=f"approved_at: {APPROVED_AT}\n")
        self.mine(bare)
        self.close_on_main(bare)
        self.assertEqual("gone", self.closed_after_delete()[0])

    def test_an_approval_time_in_a_block_scalar_is_not_read(self):
        # `rationale: |` の下の行は欄ではない。古い形どうしは ccnavi_approved の中の値で比べる。
        noisy = old_copy_text().replace(
            "ccnavi_approved:", "rationale: |\n  approved_at: 1999-01-01\nccnavi_approved:"
        )
        self.mine(noisy)
        self.close_on_main(old_copy_text())
        self.assertEqual("closed", self.closed_after_delete()[0])

    def test_a_crlf_done_copy_is_read(self):
        self.close_on_main(copy_text().replace("\n", "\r\n"))
        self.assertEqual("closed", self.closed_after_delete()[0])

    def test_old_copies_on_both_sides_compare_the_approval_time(self):
        # 承認で欄を書いていた頃の形どうしは、移行の間だけ approved_at で比べる。
        self.mine(old_copy_text())
        self.close_on_main(old_copy_text())
        self.assertEqual("closed", self.closed_after_delete()[0])

    def test_old_copies_with_another_approval_time_are_not_closed(self):
        self.mine(old_copy_text())
        self.close_on_main(old_copy_text(approved_at="2020-01-01T00:00:00+0900"))
        self.assertEqual("gone", self.closed_after_delete()[0])

    def test_an_old_done_copy_is_not_matched_with_a_new_one(self):
        # 片方に着手の欄があれば approved_at では比べない。
        self.close_on_main(old_copy_text())
        self.assertEqual("gone", self.closed_after_delete()[0])

    def test_a_parent_missing_in_the_parent_tree_is_not_closed(self):
        # 承認前の提案だけが残る親のワークツリー（この親子のチケットは閉じようがない）。
        os.makedirs(os.path.join(self.tree, "wip", "proposals", "todo"))
        git(self.tree, "mv", "--", COPY, f"wip/proposals/todo/{PARENT}.md")
        git(self.tree, "commit", "-q", "-m", "back to todo")
        git(self.tree, "push", "-q", "origin", PARENT)
        self.close_on_main()
        state, done = self.closed_after_delete()
        self.assertEqual("gone", state, done.stdout + done.stderr)

    def test_a_child_copy_in_done_is_not_the_parent(self):
        self.review_says("none")
        self.keep_record()
        self.remote_commit(
            "main",
            f".ccnavi/approved/done/{PARENT}.md",
            copy_text().replace("ticket: i0001\n", "ticket: i0001\nparent: x\n"),
        )
        self.delete_remote_branch(PARENT)
        self.sync(PARENT)
        self.assertEqual("gone", fields(self.record)["state"])

    def test_a_branch_gone_without_a_closed_record_stops_the_family(self):
        self.review_says("none")
        self.keep_record()
        kept = fields(self.record)["sha"]
        self.delete_remote_branch(PARENT)
        done = self.sync(PARENT)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("状態を決められない", done.stdout)
        self.assertIn("戻し方 1", done.stdout)
        self.assertIn(f"git push origin {kept}:refs/heads/{PARENT}", done.stdout)
        self.assertIn("戻し方 2", done.stdout)
        self.assertIn("--forget i0001 を打つと親子のチケットの取り込み状態が消える", done.stdout)
        record = fields(self.record)
        self.assertEqual("gone", record["state"])
        self.assertEqual(kept, record["sha"])

    def test_a_pushed_branch_without_a_record_stops_without_writing_gone(self):
        # 送った形跡（origin/P・追跡の設定）はあるが、取り込み状態の無い親子のチケット
        # （取り込み状態を作る仕組みが入る前に送ったもの）。
        self.review_says("none")
        self.delete_remote_branch(PARENT)
        done = self.sync(PARENT)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("取り込み状態は作らずに止めた", done.stdout)
        self.assertFalse(os.path.exists(self.record))

    def test_an_unknown_merge_answer_does_not_write_gone(self):
        # API が落ちた・道具が無い。none のときだけ gone を書く。
        for text, code in (("unknown", 3), ("", 2), ("", 0), ("merged?", 0)):
            with self.subTest(text=text, code=code):
                self.review_says(text, code)
                self.keep_record()
                self.delete_remote_branch(PARENT) if self.sha(
                    self.remote, f"refs/heads/{PARENT}"
                ) else None
                done = self.sync(PARENT)
                self.assertEqual(1, done.returncode, done.stdout + done.stderr)
                self.assertIn("確かめられなかった", done.stdout)
                self.assertEqual("present", fields(self.record)["state"])

    def test_a_restored_branch_is_present_again(self):
        self.keep_record("gone")
        done = self.sync(PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual("present", fields(self.record)["state"])

    def test_a_merged_request_is_closed_without_a_done_copy(self):
        # 統合先には閉じたチケットを残さない（ready が退避して消し、squash でマージする）ので、
        # MR がマージ済みと分かれば閉じた親子のチケット。消えたとは言わない。
        self.review_says("merged 42")
        self.keep_record()
        self.delete_remote_branch(PARENT)
        done = self.sync(PARENT, CCNAVI_SYNC_RETRIES="1")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("マージ済み", done.stdout)
        self.assertIn("42", done.stdout)
        self.assertIn("閉じた親子のチケット", done.stdout)
        self.assertNotIn("戻し方", done.stdout)
        self.assertEqual("closed", fields(self.record)["state"])

    def test_a_merged_answer_does_not_wait_for_the_integration(self):
        # 統合先の done/ を待つ確かめ直しは、マージ済みかの答えが得られないときだけ。
        self.review_says("merged 42")
        self.keep_record()
        self.delete_remote_branch(PARENT)
        started = time.monotonic()
        done = self.sync(PARENT, CCNAVI_SYNC_RETRIES="3", CCNAVI_SYNC_RETRY_WAIT="2")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual("closed", fields(self.record)["state"])

    def archive_locally(self, text):
        write(os.path.join(self.ws, "logs", "archive", "self", "done", f"{PARENT}.md"), text)

    def test_a_parent_in_the_local_archive_is_closed_when_merge_is_unknown(self):
        # マージ済みかを確かめられないときだけ、手元の退避（ready が移した先）の親で補う。
        self.review_says("unknown", 3)
        self.keep_record()
        self.archive_locally(copy_text())
        self.delete_remote_branch(PARENT)
        done = self.sync(PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("logs/archive/self/done/", done.stdout)
        self.assertIn("閉じた親子のチケット", done.stdout)
        self.assertEqual("closed", fields(self.record)["state"])

    def archive_the_parent(self):
        """ready の後の状態。親チケットをツリーから消して push し、手元の退避に置く。"""
        rel = f".ccnavi/approved/doing/{PARENT}.md"
        self.archive_locally(copy_text())
        git(self.tree, "rm", "-q", "--", rel)
        git(self.tree, "commit", "-q", "-m", "退避")
        git(self.tree, "push", "-q", "origin", PARENT)

    def test_a_parent_tree_whose_copy_was_archived_is_still_a_parent_tree(self):
        self.keep_record()
        self.archive_the_parent()
        done = self.sync(PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual("present", fields(self.record)["state"])
        listed = self.sync()
        self.assertEqual(0, listed.returncode, listed.stdout + listed.stderr)
        self.assertIn(PARENT, listed.stdout)

    def test_the_local_archive_does_not_override_an_unmerged_answer(self):
        # マージされていないと分かれば、退避があっても閉じたとは読まない。
        self.review_says("none")
        self.keep_record()
        self.archive_locally(copy_text())
        self.delete_remote_branch(PARENT)
        self.sync(PARENT)
        self.assertEqual("gone", fields(self.record)["state"])

    def test_an_archive_of_another_approval_is_not_this_family(self):
        # 親のワークツリーに親チケットが残っていれば、着手の欄（base_sha）でも
        # 同じ親と言えるときだけ。
        self.review_says("unknown", 3)
        self.keep_record()
        self.archive_locally(copy_text(base_sha="0000000"))
        self.delete_remote_branch(PARENT)
        done = self.sync(PARENT)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertEqual("present", fields(self.record)["state"])

    def test_a_child_in_the_local_archive_is_not_the_parent(self):
        self.review_says("unknown", 3)
        self.keep_record()
        self.archive_locally(copy_text().replace("ticket: i0001\n", "ticket: i0001\nparent: x\n"))
        self.delete_remote_branch(PARENT)
        self.sync(PARENT)
        self.assertEqual("present", fields(self.record)["state"])

    def test_fields_in_the_body_of_the_archive_are_not_read(self):
        # 退避の親チケットの ticket: と parent: も frontmatter の行頭から拾う。本文に parent: の行が
        # あっても子とは読まず、ticket: の注記は値に含めない。
        self.review_says("unknown", 3)
        self.keep_record()
        archived = copy_text(body="parent: x\n").replace(
            f"ticket: {PARENT}\n", f"ticket: {PARENT} # 親\n"
        )
        self.archive_locally(archived)
        self.delete_remote_branch(PARENT)
        done = self.sync(PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual("closed", fields(self.record)["state"])

    def test_branch_names_are_matched_exactly(self):
        # i0001-x があっても i0001 があることにはならない（前方一致で取り違えない）。
        self.review_says("none")
        self.keep_record()
        pusher = self.pusher()
        git(pusher, "push", "-q", "origin", f"origin/{PARENT}:refs/heads/{PARENT}-x")
        self.delete_remote_branch(PARENT)
        done = self.sync(PARENT)
        self.assertEqual("gone", fields(self.record)["state"], done.stdout)

    # ---- 取り込み状態の寿命（削除せずに残し、消すのはユーザの `--forget` だけ）

    def test_records_of_removed_worktrees_are_kept_as_tombstones(self):
        # 親のワークツリーを片付けて sync を打っても、gone の取り込み状態は消えない。
        self.keep_record("gone")
        git(self.ws, "worktree", "remove", "--force", self.tree)
        done = self.sync()
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertNotIn("取り込み状態を消した", done.stdout)
        self.assertEqual("gone", fields(self.record)["state"])
        # 同じ名前で切り直しても、取り込み状態は gone のまま（push は ccnavi-git.sh が止める）。
        self.parent_tree(PARENT + "x")  # 別の親子のチケットは触らない
        self.assertEqual("gone", fields(self.record)["state"])

    def test_forget_removes_only_a_record_whose_parent_tree_is_gone(self):
        self.keep_record("gone")
        refused = self.sync("--forget", PARENT)
        self.assertEqual(1, refused.returncode, refused.stdout + refused.stderr)
        self.assertIn("まだある", refused.stdout)
        self.assertTrue(os.path.exists(self.record))
        git(self.ws, "worktree", "remove", "--force", self.tree)
        done = self.sync("--forget", PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("state gone", done.stdout)
        self.assertFalse(os.path.exists(self.record))
        again = self.sync("--forget", PARENT)
        self.assertEqual(0, again.returncode)
        self.assertIn("取り込み状態は無い", again.stdout)

    def test_forget_needs_a_name_and_does_not_touch_the_network(self):
        self.assertEqual(2, self.sync("--forget").returncode)
        self.assertEqual(2, self.sync("--forget", "../x").returncode)
        # origin が無くても消せる（ネットワークを使わない）。
        self.keep_record("gone")
        git(self.ws, "worktree", "remove", "--force", self.tree)
        git(self.ws, "remote", "remove", "origin")
        done = self.sync("--forget", PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertFalse(os.path.exists(self.record))

    # ---- 統合先（設定か、無ければホストのデフォルトブランチ）と、
    # 統合先の取り込み結果（最後に取り込んだ先頭）

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

    def test_the_integration_mirror_copies_what_judging_reads(self):
        files = {
            ".ccnavi/approved/done/old.md": "old\n",
            ".ccnavi/common/rules.yml": "rules\n",
            ".ccnavi/config/phases.yml": "phases\n",
            ".claude/settings.json": "{}\n",
            "src/app.py": "x\n",
            # export-ignore・eol の属性があっても、コミットの中身そのものをコピーする。
            ".gitattributes": ".ccnavi/common/rules.yml export-ignore\n*.yml eol=crlf\n",
        }
        self.put_on_main(files)
        done = self.sync(PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        for rel in (
            ".ccnavi/approved/done/old.md",
            ".ccnavi/common/rules.yml",
            ".ccnavi/config/phases.yml",
            ".claude/settings.json",
        ):
            with open(os.path.join(self.mirror, rel), "rb") as f:
                self.assertEqual(files[rel].encode(), f.read())
        self.assertFalse(os.path.exists(os.path.join(self.mirror, "src")))
        self.assertFalse(os.path.exists(os.path.join(self.mirror, "README.md")))

    def test_links_are_not_copied_into_the_mirror(self):
        # シンボリックリンクは統合先の取り込み結果にコピーしない。
        pusher = self.pusher()
        git(pusher, "checkout", "-q", "main")
        os.makedirs(os.path.join(pusher, ".ccnavi", "approved", "done"), exist_ok=True)
        write(os.path.join(pusher, ".ccnavi", "approved", "done", "real.md"), "real\n")
        try:
            evil = os.path.join(pusher, ".ccnavi", "approved", "done", "evil.md")
            os.symlink("/etc/passwd", evil)
        except (OSError, NotImplementedError):
            self.skipTest("リンクを作れない")
        git(pusher, "add", "-A")
        git(pusher, "commit", "-q", "-m", "link")
        git(pusher, "push", "-q", "origin", "main")
        done = self.sync(PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        done_dir = os.path.join(self.mirror, ".ccnavi", "approved", "done")
        self.assertTrue(os.path.exists(os.path.join(done_dir, "real.md")))
        self.assertFalse(os.path.lexists(os.path.join(done_dir, "evil.md")))

    def test_the_mirror_is_kept_when_the_integration_head_is_the_same(self):
        self.put_on_main({".ccnavi/common/rules.yml": "rules\n"})
        self.assertEqual(0, self.sync(PARENT).returncode)
        marker = write(os.path.join(self.mirror, "kept-marker"), "x")
        left = os.path.join(self.state, "sync", "self", "integration.tmp.99999")
        os.makedirs(left)
        self.assertEqual(0, self.sync(PARENT).returncode)
        self.assertTrue(os.path.exists(marker), "先頭が同じなのにコピーし直した")
        self.assertFalse(os.path.exists(left), "前の回の残りを消していない")
        self.put_on_main({".ccnavi/common/rules.yml": "rules 2\n"})
        self.assertEqual(0, self.sync(PARENT).returncode)
        self.assertFalse(os.path.exists(marker))

    def test_a_configured_integration_branch_is_used_and_named(self):
        git(self.pusher(), "push", "-q", "origin", "origin/main:refs/heads/develop")
        self.remote_commit("develop", ".ccnavi/approved/done/d.md", "d\n")
        done = self.sync(PARENT, CCNAVI_INTEGRATION_BRANCH="develop")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("統合先: develop（環境変数 CCNAVI_INTEGRATION_BRANCH）", done.stdout)
        head_of = fields(os.path.join(self.mirror, "head"))
        self.assertEqual("develop", head_of["branch"])
        self.assertEqual("env", head_of["source"])
        self.assertTrue(os.path.exists(os.path.join(self.mirror, ".ccnavi/approved/done/d.md")))

    def test_a_configured_integration_branch_missing_on_the_remote_stops(self):
        head = self.remote_commit(PARENT, "theirs.txt", "theirs\n")
        before = self.sha(self.tree, "HEAD")
        done = self.sync(PARENT, CCNAVI_INTEGRATION_BRANCH="nope")
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        said = "統合先 nope（環境変数 CCNAVI_INTEGRATION_BRANCH）がリモートに無い"
        self.assertIn(said, done.stdout)
        self.assertEqual(before, self.sha(self.tree, "HEAD"))
        self.assertNotEqual(head, before)
        self.assertFalse(os.path.exists(self.mirror))
        self.assertFalse(os.path.exists(self.record))

    def test_settings_local_json_names_the_integration_branch(self):
        # ユーザが端末で打つ sh には settings.local.json の env が反映されない。
        # 実行ファイルが読んで渡す。
        git(self.pusher(), "push", "-q", "origin", "origin/main:refs/heads/develop")
        write(
            os.path.join(self.ws, ".claude", "settings.local.json"),
            '{"env": {"CCNAVI_INTEGRATION_BRANCH": "develop"}}\n',
        )
        launcher = self.launcher()
        done = self.sync(PARENT, CCNAVI_BIN_PATH=launcher)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("統合先: develop（.claude/settings.local.json", done.stdout)
        self.assertEqual("settings.local.json", fields(os.path.join(self.mirror, "head"))["source"])
        # 環境変数が先。
        done = self.sync(PARENT, CCNAVI_BIN_PATH=launcher, CCNAVI_INTEGRATION_BRANCH="main")
        self.assertIn("統合先: main（環境変数", done.stdout)

    def test_a_failing_executable_stops_instead_of_using_the_default(self):
        # 実行ファイルが在るのに答えなければ、統合先を何も言わずに既定に戻さない。
        launcher = self.launcher("#!/bin/sh\necho broken >&2\nexit 1\n")
        done = self.sync(PARENT, CCNAVI_BIN_PATH=launcher)
        self.assertEqual(2, done.returncode, done.stdout + done.stderr)
        self.assertIn("返さなかった", done.stderr)
        self.assertFalse(os.path.exists(self.mirror))

    def test_the_host_default_branch_is_read_from_the_remote(self):
        # 手元の origin/HEAD が無くても、ls-remote --symref でホストの既定を読む。
        git(self.pusher(), "push", "-q", "origin", "origin/main:refs/heads/trunk")
        git(self._tmp.name, "--git-dir", self.remote, "symbolic-ref", "HEAD", "refs/heads/trunk")
        git(self.ws, "remote", "set-head", "origin", "--delete")
        done = self.sync(PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("統合先: trunk（ホストのデフォルトブランチ）", done.stdout)

    # ---- 止まる・対象外

    def test_an_unreachable_remote_stops(self):
        git(self.ws, "remote", "set-url", "origin", os.path.join(self._tmp.name, "nowhere.git"))
        done = self.sync(PARENT)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("取ってこられなかった", done.stdout)
        self.assertFalse(os.path.exists(self.record))

    def test_a_project_without_families_does_not_fail_the_run(self):
        # 引数を省いた回で、親子のチケットの無いプロジェクトの ls-remote が落ちても 1 にしない。
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
        self.assertIn("origin が無い", done.stdout)
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
        self.assertIn("worktree add .claude/worktrees/i0002 -b i0002 origin/i0002", done.stdout)

    def test_bad_names_are_refused(self):
        for bad in ("../x", "a/b", "-x", "a..b"):
            with self.subTest(bad=bad):
                done = self.sync(bad)
                self.assertEqual(2, done.returncode, done.stdout + done.stderr)

    # ---- ロック（mkdir で取り、ホスト名・pid・開始時刻を書く。古いロックは mv で強制取得する）

    def test_a_held_lock_stops_the_family(self):
        lock = self.own_lock(os.getpid(), int(time.time()))
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
        # 先頭の 0 を 8 進に読んで落ちない。pid を確かめられない（別の OS）ので時刻で古いと見る。
        lock = self.own_lock("0123", "0000000001", os_part="OtherOS")
        done = self.sync(PARENT, CCNAVI_LOCK_WAIT="0")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertFalse(os.path.exists(lock))

    def test_a_live_owner_is_not_robbed_after_ten_minutes(self):
        # 同じ機械で持ち主が生きていれば、10 分を過ぎても強制取得しない。
        lock = self.own_lock(os.getpid(), int(time.time()) - 3600)
        done = self.sync(PARENT, CCNAVI_LOCK_WAIT="0")
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertTrue(os.path.isdir(lock))

    def test_an_old_lock_of_another_os_is_taken_over_by_time(self):
        lock = self.own_lock(os.getpid(), int(time.time()) - 3600, os_part="OtherOS")
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
        lock = self.own_lock(os.getpid(), int(time.time()))
        done = self.sync(PARENT, CCNAVI_LOCK_WAIT="0", CCNAVI_LOCK_HELD=f"self/{PARENT}:1-2")
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("ロックを持っている", done.stdout)
        self.assertTrue(os.path.isdir(lock))

    def test_a_true_nesting_mark_is_trusted_and_left_in_place(self):
        started = int(time.time())
        lock = self.own_lock(os.getpid(), started)
        mark = f"{os.getpid()}-{started}"
        done = self.sync(PARENT, CCNAVI_LOCK_WAIT="0", CCNAVI_LOCK_HELD=f"self/{PARENT}:{mark}")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertTrue(os.path.isdir(lock), "入れ子で取ったロックを外した")

    @unittest.skipIf(DASH is None, "dash が無い")
    def test_a_terminated_run_releases_its_lock_under_dash(self):
        # dash は EXIT の trap を TERM で走らせない。TERM でもロックを外す。
        write(os.path.join(self.scripts, "ccnavi-review.sh"), "#!/bin/sh\nsleep 30\n")
        self.keep_record()
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

    `.ccnavi/scripts/` が写す版（i0064-10）になる前は落ちる。写す前の sh は、
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
        """リモートに親の 1 件、統合先に done/ と層、提案だけの家族、家族の無いプロジェクト。"""
        self.head = self.remote_commit(PARENT, "theirs.txt", "theirs\n")
        self.remote_commit("main", ".ccnavi/approved/done/old.md", "old\n")
        self.remote_commit("main", ".ccnavi/config/phases.yml", "phases\n")
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
        self.assertEqual("present", fields(self.record)["state"])
        self.assertEqual(self.head, fields(self.record)["sha"])
        other = os.path.join(self.state, "sync", "self", "families", "i0002")
        self.assertEqual("present", fields(other)["state"])
        # 統合先の控えは既定の置き場の done/ と層（.ccnavi/config）を写す。
        for rel in (".ccnavi/approved/done/old.md", ".ccnavi/config/phases.yml"):
            self.assertTrue(os.path.isfile(os.path.join(self.mirror, rel)), rel)
        # プロジェクトは既定の projects/ の下で見つける。
        self.assertIn("p: リモートのブランチの一覧を取ってこられなかった", done.stdout)
        # env が指す場所には何も作らない。
        self.assertFalse(os.path.exists(self.outside), "CCNAVI_STATE を読んでいる")
        for rel in ("tickets", ".nav", "repos"):
            self.assertFalse(os.path.exists(os.path.join(self.ws, rel)), rel)
            self.assertFalse(os.path.exists(os.path.join(self.mirror, rel)), rel)

    def test_the_variables_do_not_move_the_places(self):
        """実行ファイルが `sync paths` に答える道。env を入れても既定の置き場で取り込む。"""
        self.arrange()
        done = self.sync(CCNAVI_BIN_PATH=self.launcher(), **self.moved())
        self.assertDefaultPlaces(done)

    def test_the_fallback_without_an_answer_uses_the_defaults(self):
        """実行ファイルが `sync paths` に空で答える道（予備）。sh が既定の綴りを直に使う。"""
        self.arrange()
        done = self.sync(CCNAVI_BIN_PATH=self.launcher("#!/bin/sh\nexit 0\n"), **self.moved())
        self.assertDefaultPlaces(done)


if __name__ == "__main__":
    unittest.main()
