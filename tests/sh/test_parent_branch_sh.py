"""`/` を含む既存のブランチ（`feature/123-login`）で、識別子 `feature-123-login` の親チケットの
作業が、承認 → 着手 → push → 取り込み → 終える と通ること。親のブランチ名は親のワークツリーの
HEAD から読む。

使い捨てのワークスペースと bare のリモートを組み、sh（ccnavi-git.sh・ccnavi-sync.sh・
ccnavi-ticket.sh・ccnavi-fetch.sh）を外から呼ぶ。実行ファイルはこのツリーのソースを
`python -m ccnavi` で起こす（tests/sh/test_c1_sh.py と同じ形）。ユーザが既に作ったブランチ
`feature/123-login` を、`.claude/worktrees/feature-123-login` が直接チェックアウトする。

見るのは次のとおり。

1. `worktree add` は、行き先の名前がブランチ名の `/` を `-` に置き換えたものになる形だけを通す。
   識別子のブランチは作らず、移る手順（`switch`）も無い
2. 承認画面にブランチの行は出ず、既存のブランチとぶつかる warn も出ない
3. 最初の push が取り込み状態を識別子の鍵で作り、中の `branch`（キャッシュ）に親のブランチ名を書く
4. 取り込み（ccnavi-sync.sh <識別子>）・着手と終える（C1）・セッションの頭の早送り
   （ccnavi-fetch.sh）が、HEAD から読んだ親のブランチ名の ref を使う
5. 親のワークツリーでは、今のブランチと、変換するとその識別子になるブランチのほかへ移れない
6. 子のワークツリーからは送れない（子のブランチは子の識別子）
7. HEAD が合わない・取り込み状態の `branch` が食い違う・`branch:` の欄が残る（エラー）場合の動き。
   取り込み状態の `branch` はキャッシュで、判定に使わず、送るか取り込むと書き直る
8. 消えた（取り込み状態が gone の）親のブランチへは送れない
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

from tests import ROOT, SRC, common_path, config_path
from tests.ticketsample import PHASES, RULES, child_text, parent_text

SHELL = shutil.which("sh") or shutil.which("bash")
GIT = shutil.which("git")
SH_DIR = os.path.join(ROOT, ".ccnavi", "scripts")
CONFIG = (
    ("user.email", "t@example.invalid"),
    ("user.name", "t"),
    ("commit.gpgsign", "false"),
)
PARENT = "feature-123-login"
BRANCH = "feature/123-login"
CHILD = f"{PARENT}-01-01"
APPROVED = ".ccnavi/approved"
TODO = "wip/proposals/todo"

EXE = """#!/bin/sh
PYTHONPATH='{src}' exec '{python}' -m ccnavi --guard-ticket-approval disable "$@"
"""


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


def with_branch(text, branch):
    """親の提案の frontmatter に古い `branch:` を足す。"""
    head, rest = text.split("\n", 3)[:3], text.split("\n", 3)[3]
    return "\n".join([*head, f"branch: {branch}", rest])


@unittest.skipIf(SHELL is None or GIT is None, "sh か git が無い")
class ParentBranchShTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = self._tmp.name
        self.ws = os.path.join(base, "ws")
        self.scripts = os.path.join(self.ws, ".ccnavi", "scripts")
        os.makedirs(self.scripts)
        for name in os.listdir(SH_DIR):
            if name.endswith(".sh"):
                shutil.copy(os.path.join(SH_DIR, name), self.scripts)
        git(base, "init", "-q", "-b", "main", self.ws)
        for key, value in CONFIG:
            git(self.ws, "config", key, value)
        write(os.path.join(self.ws, ".gitignore"), ".claude/worktrees/\nlogs/\n")
        write(common_path(self.ws, "rules"), json.dumps(RULES))
        write(config_path(self.ws, "phases"), PHASES)
        write(os.path.join(self.ws, "src", "keep.py"), "print(1)\n")
        git(self.ws, "add", "-A")
        git(self.ws, "commit", "-q", "-m", "seed")
        self.remote = os.path.join(base, "origin.git")
        git(base, "init", "-q", "--bare", "-b", "main", self.remote)
        git(self.ws, "remote", "add", "origin", self.remote)
        git(self.ws, "push", "-q", "-u", "origin", "main")
        git(self.ws, "remote", "set-head", "origin", "main")
        self.bin = os.path.join(base, "bin", "ccnavi")
        write(self.bin, EXE.format(src=SRC, python=sys.executable))
        os.chmod(self.bin, os.stat(self.bin).st_mode | stat.S_IXUSR | stat.S_IXGRP)
        self.state = os.path.join(self.ws, "logs", "state")
        self.record = os.path.join(self.state, "sync", "self", "families", PARENT)
        self.tree = os.path.join(self.ws, ".claude", "worktrees", PARENT)
        # ユーザが既に作って送ってあるブランチ（`/` を含む）。
        self.remote_commit("src/login.py", "print('login')\n", branch=BRANCH, start="main")
        # worktree add は、統合先から切るとき最後の fetch が新しいことを求める。
        git(self.ws, "fetch", "-q", "origin")

    # ---- 道具

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        env.pop("CLAUDE_PROJECT_DIR", None)
        env.update(
            {
                "CCNAVI_BIN_PATH": self.bin,
                "CCNAVI_SYNC_RETRIES": "0",
                "CCNAVI_SYNC_RETRY_WAIT": "0",
                "CCNAVI_LOCK_WAIT": "2",
            }
        )
        env.update(extra)
        return env

    def exe(self, *args, cwd=None):
        return subprocess.run(
            [SHELL, self.bin, "--root", self.ws, *args],
            cwd=cwd or self.ws,
            env=self.env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def sh(self, name, *args, cwd=None):
        return subprocess.run(
            [SHELL, os.path.join(self.scripts, name), *args],
            cwd=cwd or self.ws,
            env=self.env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            input="",
        )

    def ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def refused(self, result):
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        return result

    def sha(self, tree, ref):
        done = git(tree, "rev-parse", "--verify", "-q", ref, check=False)
        return done.stdout.strip() if done.returncode == 0 else ""

    def remote_sha(self, branch=BRANCH):
        done = git(self.ws, "ls-remote", "origin", f"refs/heads/{branch}")
        return done.stdout.split("\t")[0] if done.stdout else ""

    def pusher(self):
        path = os.path.join(self._tmp.name, "pusher")
        if not os.path.isdir(path):
            git(self._tmp.name, "clone", "-q", self.remote, path)
            for key, value in CONFIG:
                git(path, "config", key, value)
        git(path, "fetch", "-q", "--prune", "origin")
        return path

    def remote_commit(self, rel, text, branch=BRANCH, start=None):
        """リモートの <branch> に 1 件積む（他の機械・Chrome の代わり）。"""
        path = self.pusher()
        git(path, "checkout", "-q", "-B", branch, f"origin/{start or branch}")
        write(os.path.join(path, rel), text)
        git(path, "add", "--", rel)
        git(path, "commit", "-q", "-m", f"remote {rel}")
        git(path, "push", "-q", "origin", branch)
        return self.sha(path, "HEAD")

    def head_branch(self, tree):
        return git(tree, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    def add_parent_tree(self):
        """既存のブランチを直接チェックアウトして親のワークツリーを切り、提案と子の提案を
        書いてコミットする。"""
        self.ok(self.sh("ccnavi-git.sh", "fetch", "origin", BRANCH))
        self.ok(self.sh("ccnavi-git.sh", "worktree", "add", f".claude/worktrees/{PARENT}", BRANCH))
        self.propose()

    def propose(self, tree=None, text=None):
        tree = tree or self.tree
        write(
            os.path.join(tree, TODO, PARENT + ".md"),
            text or parent_text(PARENT, ["design"], allow=("src/*", "wip/*")),
        )
        write(
            os.path.join(tree, TODO, CHILD + ".md"),
            child_text(CHILD, PARENT, 1, ("wip/design/*",), False),
        )
        git(tree, "add", "-A")
        git(tree, "commit", "-q", "-m", "propose")

    def approve(self):
        preview = self.ok(self.exe("--agree", "--preview", "--json"))
        body = json.loads(preview.stdout)
        done = self.exe("--agree", "--yes", f"{PARENT},{CHILD}", "--json")
        self.ok(done)
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "-q", "-m", "approve")
        return body

    def imported_family(self):
        """承認して送り、取り込み済みにした親子のチケット。"""
        self.add_parent_tree()
        self.approve()
        self.ok(self.sh("ccnavi-git.sh", "push", "-u", "origin", BRANCH, cwd=self.tree))
        self.ok(self.sh("ccnavi-sync.sh", PARENT))

    def write_approved_copy(self, extra=""):
        """承認済みの親チケット（古い `branch:` 付きなど）を手で書く。"""
        text = parent_text(PARENT, ["design"], allow=("src/*",))
        if extra:
            text = with_branch(text, extra)
        text = text.replace(
            "---\n\n本文", "ccnavi_approved:\n  approved_at: 2026-10-01T00:00:00+0900\n---\n\n本文"
        )
        write(os.path.join(self.tree, APPROVED, "doing", PARENT + ".md"), text)
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "-q", "-m", "copy")

    # ---- 主な経路

    def test_the_main_path_on_an_existing_branch_with_a_slash(self):
        # 1. 行き先の名前は、ブランチ名の / を - に置き換えたもの。違う名前は断る
        self.ok(self.sh("ccnavi-git.sh", "fetch", "origin", BRANCH))
        refused = self.refused(
            self.sh("ccnavi-git.sh", "worktree", "add", ".claude/worktrees/other", BRANCH)
        )
        self.assertIn("/ を - に置き換えたもの", refused.stderr)
        refused = self.refused(
            self.sh(
                "ccnavi-git.sh", "worktree", "add", ".claude/worktrees/x", "-b", "hotfix/45", "main"
            )
        )
        self.assertIn("/ を - に置き換えたもの", refused.stderr)
        self.add_parent_tree()
        self.assertEqual(self.head_branch(self.tree), BRANCH)
        self.assertEqual(self.sha(self.ws, f"refs/heads/{PARENT}"), "")

        # 2. 親のブランチ名は HEAD から読む。承認画面にブランチの行は出ず、ぶつかりの warn も出ない
        family = self.ok(self.exe("c1", "family", PARENT))
        self.assertIn(f"branch {BRANCH}\n", family.stdout)
        verify = self.exe("--agree", "--preview", "--verify", "--json")
        body = json.loads(verify.stdout)
        entry = next(e for e in body["batch"] if e["ticket"] == PARENT)
        self.assertNotIn("branch", entry)
        self.assertNotIn("■ ブランチ", body["text"])
        self.assertEqual(body["branch_warnings"], [])
        self.approve()
        self.assertTrue(os.path.isfile(os.path.join(self.tree, APPROVED, "doing", PARENT + ".md")))
        self.assertTrue(os.path.isfile(os.path.join(self.tree, "src", "login.py")))
        self.assertEqual(git(self.tree, "status", "--porcelain").stdout, "")

        # 3. 最初の push が取り込み状態を識別子の鍵で作り、中の branch に親のブランチ名を書く
        pushed = self.ok(self.sh("ccnavi-git.sh", "push", "-u", "origin", BRANCH, cwd=self.tree))
        self.assertIn("取り込み状態を作った", pushed.stdout)
        record = fields(self.record)
        self.assertEqual((record["branch"], record["state"]), (BRANCH, "present"))
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        family = self.ok(self.exe("c1", "family", PARENT))
        self.assertIn("target yes\n", family.stdout)

        # 4. 取り込みと着手（C1 は親のブランチ名へ送る）
        synced = self.ok(self.sh("ccnavi-sync.sh", PARENT))
        self.assertIn("リモートと同じ", synced.stdout)
        started = self.ok(self.sh("ccnavi-ticket.sh", "start", PARENT))
        self.assertIn("に着手した", started.stdout)
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertEqual(fields(self.record)["sha"], self.sha(self.tree, "HEAD"))
        self.assertEqual(self.remote_sha(PARENT), "")

        # 5. 親のワークツリーでは、今のブランチのほかへ移れない。今のブランチへは移れる
        moved = self.refused(self.sh("ccnavi-git.sh", "switch", "--create", "other", cwd=self.tree))
        self.assertIn("別のブランチ（other）へ移れません", moved.stderr)
        self.ok(self.sh("ccnavi-git.sh", "switch", BRANCH, cwd=self.tree))
        self.assertEqual(self.head_branch(self.tree), BRANCH)

        # 6. 子は子の識別子のブランチで、親のブランチから切る。子からは送らない
        self.ok(
            self.sh(
                "ccnavi-git.sh",
                "worktree",
                "add",
                f".claude/worktrees/{CHILD}",
                "-b",
                CHILD,
                BRANCH,
            )
        )
        child_tree = os.path.join(self.ws, ".claude", "worktrees", CHILD)
        self.ok(self.sh("ccnavi-ticket.sh", "start", CHILD))
        write(os.path.join(child_tree, "wip", "design", "plan.md"), "# 設計\n")
        git(child_tree, "add", "-A")
        git(child_tree, "commit", "-q", "-m", "design")
        self.refused(self.sh("ccnavi-git.sh", "push", "-u", "origin", CHILD, cwd=child_tree))
        finished = self.ok(self.sh("ccnavi-ticket.sh", "finish", CHILD))
        self.assertTrue(
            os.path.isfile(os.path.join(self.tree, "wip", "proposals", "review", CHILD + ".md")),
            finished.stdout + finished.stderr,
        )
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))

        # 7. 他の機械の push を取り込む（ccnavi-sync.sh とセッションの頭の早送り）
        self.remote_commit("wip/note-1.md", "1\n")
        synced = self.ok(self.sh("ccnavi-sync.sh", PARENT))
        self.assertIn("早送りで取り込んだ", synced.stdout)
        self.assertTrue(os.path.isfile(os.path.join(self.tree, "wip", "note-1.md")))
        remote = self.remote_commit("wip/note-2.md", "2\n")
        fetched = self.ok(self.sh("ccnavi-fetch.sh"))
        self.assertIn(
            f"{PARENT}: 承認済みチケットとマーカーを 1 件分だけ新しくした", fetched.stdout
        )
        self.assertEqual(self.sha(self.tree, "HEAD"), remote)

        lint = self.exe("--lint")
        self.assertNotIn("の上にある", lint.stdout + lint.stderr)

    def test_a_new_branch_with_a_slash_is_cut_into_the_converted_name(self):
        self.ok(
            self.sh(
                "ccnavi-git.sh",
                "worktree",
                "add",
                ".claude/worktrees/hotfix-45",
                "-b",
                "hotfix/45",
                "main",
            )
        )
        tree = os.path.join(self.ws, ".claude", "worktrees", "hotfix-45")
        self.assertEqual(self.head_branch(tree), "hotfix/45")
        self.assertEqual(self.sha(self.ws, "refs/heads/hotfix-45"), "")

    def test_a_record_made_before_the_ticket_does_not_stop_the_approval(self):
        """`ccnavi-start.sh` はチケットの前に、取り込み記録（present・mr 付き）を作って
        ccnavi-sync.sh を打つ。チケットが無いので取り込まれないが、統合先の取り込み結果は書かれ、
        最初の承認（--agree）の前に ccnavi-sync.sh を打ち直さなくても通る。"""
        self.ok(self.sh("ccnavi-git.sh", "fetch", "origin", BRANCH))
        self.ok(self.sh("ccnavi-git.sh", "worktree", "add", f".claude/worktrees/{PARENT}", BRANCH))
        sha = self.sha(self.tree, "HEAD")
        write(
            self.record,
            f"remote origin\nbranch {BRANCH}\nsha {sha}\nfetched_at 1\nstate present\n"
            "reason \nmr 7\n",
        )
        synced = self.sh("ccnavi-sync.sh", PARENT)
        self.assertEqual(synced.returncode, 1, synced.stdout + synced.stderr)
        self.assertIn("親チケットも提案もありません", synced.stdout)
        self.assertTrue(
            os.path.isfile(os.path.join(self.state, "sync", "self", "integration", "head"))
        )
        self.propose()
        verify = self.exe("--agree", "--preview", "--verify", "--json")
        body = json.loads(verify.stdout)
        self.assertTrue(body["verify"]["ok"], body)
        self.approve()
        self.ok(self.sh("ccnavi-git.sh", "push", "-u", "origin", BRANCH, cwd=self.tree))
        record = fields(self.record)
        self.assertEqual((record["state"], record["mr"]), ("present", "7"))

    # ---- 取り込み状態はキャッシュ

    def test_a_record_pinned_to_another_branch_is_only_a_cache(self):
        self.imported_family()
        with open(self.record, encoding="utf-8") as f:
            text = f.read()
        write(self.record, text.replace(f"branch {BRANCH}", "branch feature/other"))
        family = self.ok(self.exe("c1", "family", PARENT))
        self.assertIn("target yes\n", family.stdout)
        self.assertIn(f"branch {BRANCH}\n", family.stdout)
        self.ok(self.sh("ccnavi-sync.sh", PARENT))
        self.assertEqual(fields(self.record)["branch"], BRANCH)

    def test_a_left_over_branch_field_is_an_error(self):
        self.add_parent_tree()
        self.write_approved_copy("origin/main")
        lint = self.exe("--lint")
        self.assertIn("`branch` は使えない欄", lint.stdout)

    def test_two_records_naming_one_branch_do_not_stop_the_push(self):
        self.imported_family()
        other = os.path.join(self.state, "sync", "self", "families", "feature-9-other")
        write(other, f"remote origin\nbranch {BRANCH}\nstate present\nreason \n")
        self.ok(self.sh("ccnavi-git.sh", "push", "-u", "origin", BRANCH, cwd=self.tree))

    # ---- HEAD が合わない

    def test_a_head_off_the_converted_name_stops_the_family(self):
        self.imported_family()
        git(self.tree, "checkout", "-q", "-b", "elsewhere")
        family = self.ok(self.exe("c1", "family", PARENT))
        self.assertIn("target stop\n", family.stdout)
        self.assertIn("branch_refused ", family.stdout)
        self.assertNotIn("\nbranch ", family.stdout)
        synced = self.sh("ccnavi-sync.sh", PARENT)
        self.assertEqual(synced.returncode, 1, synced.stdout + synced.stderr)
        self.assertIn("と合わない", synced.stdout)
        # 全部を回すときは、合わないワークツリーを飛ばす
        self.ok(self.sh("ccnavi-sync.sh"))
        # 戻せば動く
        git(self.tree, "checkout", "-q", BRANCH)
        self.ok(self.sh("ccnavi-sync.sh", PARENT))

    def test_syncing_a_family_without_its_worktree_says_so(self):
        synced = self.sh("ccnavi-sync.sh", "feature-99-none")
        self.assertEqual(synced.returncode, 1, synced.stdout + synced.stderr)
        self.assertIn(
            "親のワークツリー（.claude/worktrees/feature-99-none）がありません", synced.stdout
        )

    # ---- 止める形

    def test_a_gone_parent_branch_cannot_be_pushed(self):
        self.imported_family()
        with open(self.record, encoding="utf-8") as f:
            text = f.read()
        write(self.record, text.replace("state present", "state gone"))
        refused = self.refused(
            self.sh("ccnavi-git.sh", "push", "-u", "origin", BRANCH, cwd=self.tree)
        )
        self.assertIn(f"親子のチケット {PARENT} の取り込み状態が gone", refused.stderr)
        self.assertIn(f"ccnavi-sync.sh {PARENT}", refused.stderr)


if __name__ == "__main__":
    unittest.main()
