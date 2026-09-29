"""ccnavi-sync.sh の受入テスト（ADR-0093 の 4.2・3.6。段階 2b）。

使い捨てのワークスペースと bare のリモートを組み、sh を外から呼ぶ。リモートを動かすのは別に clone
した押し手で、ワークスペースからは「他の機械が push した」「ホストがブランチを消した」ように見える。

読み返すのは終了コード・標準出力と、git に残った ref、控え（logs/state/sync/...）の中身だけ。
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from tests import ROOT

SHELL = shutil.which("sh") or shutil.which("bash")
GIT = shutil.which("git")
SH_DIR = os.path.join(ROOT, ".ccnavi", "scripts")
SCRIPTS = ("ccnavi-sync.sh", "ccnavi-common.sh", "ccnavi-git.sh")
CONFIG = (
    ("user.email", "t@example.invalid"),
    ("user.name", "t"),
    ("commit.gpgsign", "false"),
)
PARENT = "i0001"
COPY = f".ccnavi/approved/doing/{PARENT}.md"


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


@unittest.skipIf(SHELL is None or GIT is None, "sh か git が無い")
class SyncTest(unittest.TestCase):
    """ワークスペース 1 つ（main）、bare のリモート、親のワークツリー .claude/worktrees/i0001。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = os.path.join(self._tmp.name, "ws")
        scripts = os.path.join(self.ws, ".ccnavi", "scripts")
        os.makedirs(scripts)
        for name in SCRIPTS:
            shutil.copy(os.path.join(SH_DIR, name), scripts)
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
        # 親のワークツリー。承認済みの親の写しをコミットして送ってある。
        self.tree = os.path.join(self.ws, ".claude", "worktrees", PARENT)
        git(self.ws, "worktree", "add", "-q", self.tree, "-b", PARENT)
        write(os.path.join(self.tree, COPY), f"---\nversion: 1\nticket: {PARENT}\n---\n")
        git(self.tree, "add", "--", COPY)
        git(self.tree, "commit", "-q", "-m", "copy")
        git(self.tree, "push", "-q", "-u", "origin", PARENT)
        self.state = os.path.join(self.ws, "logs", "state")
        self.record = os.path.join(self.state, "sync", "self", "families", PARENT)
        self.mirror = os.path.join(self.state, "sync", "self", "integration")

    # ---- 道具

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

    def local_commit(self, rel, text):
        write(os.path.join(self.tree, rel), text)
        git(self.tree, "add", "--", rel)
        git(self.tree, "commit", "-q", "-m", f"local {rel}")
        return self.sha(self.tree, "HEAD")

    def sync(self, *args, **extra):
        script = os.path.join(self.ws, ".ccnavi", "scripts", "ccnavi-sync.sh")
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        env.update({"CCNAVI_SYNC_RETRIES": "0", "CCNAVI_SYNC_RETRY_WAIT": "0"})
        env.update(extra)
        return subprocess.run(
            [SHELL, script, *args],
            cwd=self.ws,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def sha(self, tree, ref):
        done = git(tree, "rev-parse", "--verify", "-q", ref, check=False)
        return done.stdout.strip() if done.returncode == 0 else ""

    def keep_record(self, state="present"):
        """前に取り込んだ（控えがある）家族にする。"""
        write(
            self.record,
            f"remote origin\nbranch {PARENT}\nsha {self.sha(self.tree, 'HEAD')}\n"
            f"fetched_at 1\nstate {state}\nreason \n",
        )

    def merging(self):
        path = git(self.tree, "rev-parse", "--git-path", "MERGE_HEAD").stdout.strip()
        if not os.path.isabs(path):
            path = os.path.join(self.tree, path)
        return os.path.exists(path)

    # ---- 取り込み（P がリモートにある）

    def test_fast_forward_and_records(self):
        head = self.remote_commit(PARENT, ".ccnavi/approved/doing/i0001-01.md", "child\n")
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

    def test_diverged_branches_are_merged(self):
        theirs = self.remote_commit(PARENT, "theirs.txt", "theirs\n")
        mine = self.local_commit("mine.txt", "mine\n")
        done = self.sync(PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("merge で取り込んだ", done.stdout)
        parents = git(self.tree, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()[1:]
        self.assertEqual({theirs, mine}, set(parents))

    def test_a_conflict_is_aborted_and_left_to_a_person(self):
        header = f"---\nversion: 1\nticket: {PARENT}\n---\n"
        self.remote_commit(PARENT, COPY, header + "theirs\n")
        mine = self.local_commit(COPY, header + "mine\n")
        done = self.sync(PARENT)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("衝突", done.stdout)
        self.assertIn(COPY, done.stdout)
        self.assertEqual(mine, self.sha(self.tree, "HEAD"))
        self.assertFalse(self.merging(), "merge の途中が残っている")
        self.assertEqual("", git(self.tree, "status", "--porcelain").stdout.strip())

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
        self.assertIn("重なる", done.stdout)
        self.assertIn("README.md", done.stdout)
        self.assertEqual(before, self.sha(self.tree, "HEAD"))
        with open(os.path.join(self.tree, "README.md"), encoding="utf-8") as f:
            self.assertEqual("書きかけ\n", f.read())

    def test_no_arguments_takes_every_parent_worktree(self):
        other = os.path.join(self.ws, ".claude", "worktrees", "free")
        git(self.ws, "worktree", "add", "-q", other, "-b", "free")
        head = self.remote_commit(PARENT, "theirs.txt", "theirs\n")
        done = self.sync()
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(head, self.sha(self.tree, "HEAD"))
        self.assertTrue(os.path.exists(self.record))
        self.assertNotIn("free", done.stdout)

    # ---- P がリモートに無い（3.6）

    def test_a_family_never_pushed_is_left_as_it_is(self):
        self.delete_remote_branch(PARENT)
        done = self.sync(PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("まだ送っていない", done.stdout)
        self.assertFalse(os.path.exists(self.record))

    def test_a_merged_and_deleted_branch_is_closed(self):
        self.keep_record()
        self.remote_commit("main", f".ccnavi/approved/done/{PARENT}.md", "closed\n")
        self.delete_remote_branch(PARENT)
        done = self.sync(PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("閉じた家族", done.stdout)
        self.assertNotIn("戻し方", done.stdout)
        self.assertEqual("closed", fields(self.record)["state"])

    def test_a_branch_gone_without_a_closed_record_stops_the_family(self):
        self.keep_record()
        kept = fields(self.record)["sha"]
        self.delete_remote_branch(PARENT)
        done = self.sync(PARENT)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("状態を決められない", done.stdout)
        self.assertIn("戻し方 1", done.stdout)
        self.assertIn(f"git push origin {kept}:refs/heads/{PARENT}", done.stdout)
        self.assertIn("戻し方 2", done.stdout)
        record = fields(self.record)
        self.assertEqual("gone", record["state"])
        self.assertEqual(kept, record["sha"])

    def test_a_restored_branch_is_present_again(self):
        self.keep_record("gone")
        done = self.sync(PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual("present", fields(self.record)["state"])

    def test_a_merged_request_waits_instead_of_calling_it_gone(self):
        # MR がマージ済みと分かれば観測ずれ。消えたとは言わない。
        write(
            os.path.join(self.ws, ".ccnavi", "scripts", "ccnavi-review.sh"),
            '#!/bin/sh\n[ "$1" = merged ] && echo "merged 42"\n',
        )
        self.keep_record()
        self.delete_remote_branch(PARENT)
        done = self.sync(PARENT, CCNAVI_SYNC_RETRIES="1")
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("マージ済み", done.stdout)
        self.assertIn("42", done.stdout)
        self.assertNotIn("戻し方", done.stdout)
        self.assertEqual("present", fields(self.record)["state"])

    def test_branch_names_are_matched_exactly(self):
        # i0001-x があっても i0001 があることにはならない（前方一致で取り違えない）。
        self.keep_record()
        pusher = self.pusher()
        git(pusher, "push", "-q", "origin", f"origin/{PARENT}:refs/heads/{PARENT}-x")
        self.delete_remote_branch(PARENT)
        done = self.sync(PARENT)
        self.assertEqual("gone", fields(self.record)["state"], done.stdout)

    # ---- 統合先（D30）と統合先の控え（D26）

    def test_the_integration_mirror_copies_what_judging_reads(self):
        pusher = self.pusher()
        git(pusher, "checkout", "-q", "main")
        for rel in (
            ".ccnavi/approved/done/old.md",
            ".ccnavi/common/rules.yml",
            ".ccnavi/config/phases.yml",
            ".claude/settings.json",
            "src/app.py",
        ):
            write(os.path.join(pusher, rel), rel + "\n")
        git(pusher, "add", "-A")
        git(pusher, "commit", "-q", "-m", "layers")
        git(pusher, "push", "-q", "origin", "main")
        done = self.sync(PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        for rel in (
            ".ccnavi/approved/done/old.md",
            ".ccnavi/common/rules.yml",
            ".ccnavi/config/phases.yml",
            ".claude/settings.json",
        ):
            with open(os.path.join(self.mirror, rel), encoding="utf-8") as f:
                self.assertEqual(rel + "\n", f.read())
        self.assertFalse(os.path.exists(os.path.join(self.mirror, "src")))
        self.assertFalse(os.path.exists(os.path.join(self.mirror, "README.md")))

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
        # 人が端末で打つ sh には settings.local.json の env が効かない。実行ファイルが読んで渡す。
        git(self.pusher(), "push", "-q", "origin", "origin/main:refs/heads/develop")
        write(
            os.path.join(self.ws, ".claude", "settings.local.json"),
            '{"env": {"CCNAVI_INTEGRATION_BRANCH": "develop"}}\n',
        )
        launcher = write(
            os.path.join(self._tmp.name, "bin", "ccnavi"),
            f'#!/bin/sh\nPYTHONPATH="{ROOT}" exec "{sys.executable}" -m ccnavi "$@"\n',
        )
        os.chmod(launcher, 0o755)
        done = self.sync(PARENT, CCNAVI_BIN_PATH=launcher)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("統合先: develop（.claude/settings.local.json", done.stdout)
        self.assertEqual("settings.local.json", fields(os.path.join(self.mirror, "head"))["source"])
        # 環境変数が先。
        done = self.sync(PARENT, CCNAVI_BIN_PATH=launcher, CCNAVI_INTEGRATION_BRANCH="main")
        self.assertIn("統合先: main（環境変数", done.stdout)

    def test_without_origin_head_main_is_the_default(self):
        git(self.ws, "remote", "set-head", "origin", "--delete")
        done = self.sync(PARENT)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("統合先: main（ホストのデフォルトブランチ）", done.stdout)

    # ---- 止まる・対象外

    def test_an_unreachable_remote_stops(self):
        git(self.ws, "remote", "set-url", "origin", os.path.join(self._tmp.name, "nowhere.git"))
        done = self.sync(PARENT)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("取ってこられなかった", done.stdout)
        self.assertFalse(os.path.exists(self.record))

    def test_without_origin_nothing_happens(self):
        git(self.ws, "remote", "remove", "origin")
        done = self.sync()
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("origin が無い", done.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.state, "sync")))

    def test_a_held_lock_stops_the_family(self):
        lock = os.path.join(self.state, "locks", "self", PARENT)
        os.makedirs(lock)
        now = int(time.time())
        pid = os.getpid()
        write(os.path.join(lock, "owner"), f"{socket.gethostname()} {pid} {now} {pid}-{now}\n")
        head = self.remote_commit(PARENT, "theirs.txt", "theirs\n")
        done = self.sync(PARENT, CCNAVI_LOCK_WAIT="0")
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertIn("ロックを持っている", done.stdout)
        self.assertNotEqual(head, self.sha(self.tree, "HEAD"))
        self.assertTrue(os.path.isdir(lock), "他人のロックを外した")

    def test_a_stale_lock_is_taken_over(self):
        finished = subprocess.run(
            [sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True
        )
        dead = finished.stdout.strip()
        lock = os.path.join(self.state, "locks", "self", PARENT)
        os.makedirs(lock)
        now = int(time.time())
        write(os.path.join(lock, "owner"), f"{socket.gethostname()} {dead} {now} {dead}-{now}\n")
        head = self.remote_commit(PARENT, "theirs.txt", "theirs\n")
        done = self.sync(PARENT, CCNAVI_LOCK_WAIT="0")
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(head, self.sha(self.tree, "HEAD"))
        self.assertFalse(os.path.exists(lock), "取ったロックが残っている")

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


if __name__ == "__main__":
    unittest.main()
