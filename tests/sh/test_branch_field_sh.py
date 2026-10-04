"""親チケットの `branch:`（ADR-0100 の 5 章）で、`/` を含む既存のブランチを親のブランチにして、
承認 → ワークツリー → 着手 → push → 取り込み → 終える の主な経路が通ること。

使い捨てのワークスペースと bare のリモートを組み、sh（ccnavi-git.sh・ccnavi-sync.sh・
ccnavi-ticket.sh・ccnavi-fetch.sh）を外から呼ぶ。実行ファイルはこのツリーのソースを
`python -m ccnavi` で起こす（tests/sh/test_c1_sh.py と同じ形）。ユーザが既に作ったブランチ
`feature/123-login` を、識別子 `feature-123-login` の親チケットが `branch:` で名乗る。

見るのは次のとおり。

1. 親チケット（か提案）が名乗るまでは、識別子と違う名前のブランチをワークツリーに出せない。
   名乗れば出せる（既にあるブランチを出す形も、`-b` で切る形も）
2. 承認画面（`--agree --preview --json`）が「既存のブランチ <名前> を使う」と言い、
   既にあるブランチとぶつかる warn は出さない
3. 最初の push が家族の控えを識別子の鍵で作り、中の `branch` に親のブランチ名を書く
4. 取り込み（ccnavi-sync.sh <識別子>）・着手と終える（C1）・セッションの頭の早送り
   （ccnavi-fetch.sh）が親のブランチ名の ref を使う
5. 親のワークツリーでは親のブランチ（`branch:` の値）のほかへ移れない（識別子の名前のブランチへも）
6. 子のワークツリーからは送れない（子のブランチは子の識別子）
7. 同じ家族を名乗るブランチが 2 本あれば、権威が決まらないとして止める
8. 消えた（控えが gone の）親のブランチへは送れない
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

from tests import ROOT, common_path
from tests.ticket.test_phases import PHASES, child_text, parent_text
from tests.ticket.test_ticket import RULES

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
CHILD = f"{PARENT}-01"
APPROVED = ".ccnavi/approved"
TODO = "wip/proposals/todo"

EXE = """#!/bin/sh
PYTHONPATH='{root}' exec '{python}' -m ccnavi --guard-ticket-approval disable "$@"
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
    """親の提案の frontmatter に `branch:` を足す。"""
    head, rest = text.split("\n", 3)[:3], text.split("\n", 3)[3]
    return "\n".join([*head, f"branch: {branch}", rest])


@unittest.skipIf(SHELL is None or GIT is None, "sh か git が無い")
class BranchFieldTest(unittest.TestCase):
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
        write(common_path(self.ws, "phases"), PHASES)
        write(os.path.join(self.ws, "src", "keep.py"), "print(1)\n")
        git(self.ws, "add", "-A")
        git(self.ws, "commit", "-q", "-m", "seed")
        self.remote = os.path.join(base, "origin.git")
        git(base, "init", "-q", "--bare", "-b", "main", self.remote)
        git(self.ws, "remote", "add", "origin", self.remote)
        git(self.ws, "push", "-q", "-u", "origin", "main")
        git(self.ws, "remote", "set-head", "origin", "main")
        self.bin = os.path.join(base, "bin", "ccnavi")
        write(self.bin, EXE.format(root=ROOT, python=sys.executable))
        os.chmod(self.bin, os.stat(self.bin).st_mode | stat.S_IXUSR | stat.S_IXGRP)
        self.state = os.path.join(self.ws, "logs", "state")
        self.record = os.path.join(self.state, "sync", "self", "families", PARENT)
        self.tree = os.path.join(self.ws, ".claude", "worktrees", PARENT)
        # ユーザが既に作って送ってあるブランチ（`/` を含む）。
        self.remote_commit("src/login.py", "print('login')\n", branch=BRANCH, start="main")

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
            [self.bin, "--root", self.ws, *args],
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

    def propose_in_root(self):
        """ワークツリーを作る前の、ワークスペースルートの提案（`branch:` 付き）。"""
        return write(
            os.path.join(self.ws, TODO, PARENT + ".md"),
            with_branch(parent_text(PARENT, ["design"], allow=("src/*", "wip/*")), BRANCH),
        )

    def add_parent_tree(self):
        self.ok(self.sh("ccnavi-git.sh", "fetch", "origin", BRANCH))
        self.propose_in_root()
        self.ok(self.sh("ccnavi-git.sh", "worktree", "add", f".claude/worktrees/{PARENT}", BRANCH))
        self.assertEqual(self.head_branch(self.tree), BRANCH)

    def move_proposals(self):
        """提案を親のワークツリーへ運び（親のブランチの上で書く。ADR-0093 の 3.2）、子も足す。"""
        os.remove(os.path.join(self.ws, TODO, PARENT + ".md"))
        write(
            os.path.join(self.tree, TODO, PARENT + ".md"),
            with_branch(parent_text(PARENT, ["design"], allow=("src/*", "wip/*")), BRANCH),
        )
        write(
            os.path.join(self.tree, TODO, CHILD + ".md"),
            child_text(CHILD, PARENT, 1, ("wip/design/*",), False),
        )
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "-q", "-m", "propose")

    def approve(self):
        preview = self.ok(self.exe("--agree", "--preview", "--json"))
        body = json.loads(preview.stdout)
        done = self.exe(
            "--agree", "--yes", f"{PARENT},{CHILD}", "--digest", body["digest"], "--json"
        )
        self.ok(done)
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "-q", "-m", "approve")
        return body

    def imported_family(self):
        """承認して送り、取り込み済みにした家族。"""
        self.add_parent_tree()
        self.move_proposals()
        self.approve()
        self.ok(self.sh("ccnavi-git.sh", "push", "-u", "origin", BRANCH, cwd=self.tree))
        self.ok(self.sh("ccnavi-sync.sh", PARENT))

    # ---- 主な経路

    def test_the_main_path_on_an_existing_branch_with_a_slash(self):
        # 1. 名乗る親チケットが無ければ、識別子と違う名前のブランチは出せない
        self.ok(self.sh("ccnavi-git.sh", "fetch", "origin", BRANCH))
        refused = self.refused(
            self.sh("ccnavi-git.sh", "worktree", "add", f".claude/worktrees/{PARENT}", BRANCH)
        )
        self.assertIn(f"branch: {BRANCH}", refused.stderr)
        self.assertFalse(os.path.exists(self.tree))
        # 提案（ワークスペースルート）が branch: で名乗れば出せる
        self.propose_in_root()
        self.ok(self.sh("ccnavi-git.sh", "worktree", "add", f".claude/worktrees/{PARENT}", BRANCH))
        self.assertEqual(self.head_branch(self.tree), BRANCH)
        self.move_proposals()

        # 2. 承認画面は既にあるブランチを使うと言い、ぶつかりの warn は出さない
        verify = self.exe("--agree", "--preview", "--verify", "--json")
        body = json.loads(verify.stdout)
        entry = next(e for e in body["batch"] if e["ticket"] == PARENT)
        self.assertEqual(entry["branch"], BRANCH)
        self.assertTrue(entry["existing_branch"])
        self.assertIn(f"既存のブランチ {BRANCH} を使う", body["text"])
        self.assertEqual(body["branch_warnings"], [])
        child = next(e for e in body["batch"] if e["ticket"] == CHILD)
        self.assertEqual(child["branch"], CHILD)
        self.approve()

        # 3. 最初の push が家族の控えを識別子の鍵で作る
        pushed = self.ok(self.sh("ccnavi-git.sh", "push", "-u", "origin", BRANCH, cwd=self.tree))
        self.assertIn("家族の控えを作った", pushed.stdout)
        record = fields(self.record)
        self.assertEqual((record["branch"], record["state"]), (BRANCH, "present"))
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertEqual(self.remote_sha(PARENT), "")
        family = self.ok(self.exe("c1", "family", PARENT))
        self.assertIn(f"branch {BRANCH}\n", family.stdout)
        self.assertIn("target yes\n", family.stdout)

        # 4. 取り込みと着手（C1 は親のブランチ名へ送る）
        synced = self.ok(self.sh("ccnavi-sync.sh", PARENT))
        self.assertIn("リモートと同じ", synced.stdout)
        started = self.ok(self.sh("ccnavi-ticket.sh", "start", PARENT))
        self.assertIn("に着手した", started.stdout)
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertEqual(fields(self.record)["sha"], self.sha(self.tree, "HEAD"))
        self.assertEqual(self.remote_sha(PARENT), "")

        # 5. 親のワークツリーでは親のブランチのほかへ移れない（識別子の名前のブランチへも）
        for args in (("switch", "--create", "other"), ("checkout", "-b", PARENT)):
            with self.subTest(args=args):
                moved = self.refused(self.sh("ccnavi-git.sh", *args, cwd=self.tree))
                self.assertIn(f"親のブランチは {BRANCH}", moved.stderr)
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
        git(child_tree, "merge", "-q", "--ff-only", BRANCH)
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

        # 4（続き）. 他の機械の push を取り込む（ccnavi-sync.sh とセッションの頭の早送り）
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

        # lint は親のワークツリーが親のブランチの上に居ると読む
        lint = self.exe("--lint")
        self.assertNotIn("の上に居る", lint.stdout + lint.stderr)

    def test_a_new_branch_named_by_the_field_can_be_cut(self):
        name, branch = "feature-124-x", "hotfix/45"
        write(
            os.path.join(self.ws, TODO, name + ".md"),
            with_branch(parent_text(name, ["design"], allow=("src/*",)), branch),
        )
        self.ok(
            self.sh(
                "ccnavi-git.sh",
                "worktree",
                "add",
                f".claude/worktrees/{name}",
                "-b",
                branch,
                "main",
            )
        )
        tree = os.path.join(self.ws, ".claude", "worktrees", name)
        self.assertEqual(self.head_branch(tree), branch)
        # 名乗っていない名前は今どおり断る
        self.refused(
            self.sh(
                "ccnavi-git.sh", "worktree", "add", ".claude/worktrees/feature-125-y", "-b", "x/y"
            )
        )

    # ---- 止める形

    def test_two_branches_claiming_one_family_stop_it(self):
        self.imported_family()
        # 識別子と同じ名前のブランチに、branch: の無い親の写しを置く（同じ家族を名乗る）
        rival = os.path.join(self.ws, ".claude", "worktrees", "rival")
        git(self.ws, "worktree", "add", "-q", rival, "-b", PARENT, "main")
        write(
            os.path.join(rival, APPROVED, "doing", PARENT + ".md"),
            parent_text(PARENT, ["design"], allow=("src/*",)),
        )
        family = self.ok(self.exe("c1", "family", PARENT))
        self.assertIn("target stop\n", family.stdout)
        self.assertIn("名乗るブランチが 1 本でない", family.stdout)
        started = self.sh("ccnavi-ticket.sh", "start", PARENT)
        self.assertNotEqual(started.returncode, 0)
        self.assertIn("名乗るブランチが 1 本でない", started.stderr)

    def test_a_record_pinned_to_another_branch_stops_the_family(self):
        self.imported_family()
        with open(self.record, encoding="utf-8") as f:
            text = f.read()
        write(self.record, text.replace(f"branch {BRANCH}", "branch feature/other"))
        family = self.ok(self.exe("c1", "family", PARENT))
        self.assertIn("target stop\n", family.stdout)
        self.assertIn("控えの親のブランチ", family.stdout)
        synced = self.sh("ccnavi-sync.sh", PARENT)
        self.assertEqual(synced.returncode, 1, synced.stdout + synced.stderr)
        self.assertIn("控えの親のブランチ", synced.stdout)

    def test_a_gone_parent_branch_cannot_be_pushed(self):
        self.imported_family()
        with open(self.record, encoding="utf-8") as f:
            text = f.read()
        write(self.record, text.replace("state present", "state gone"))
        refused = self.refused(
            self.sh("ccnavi-git.sh", "push", "-u", "origin", BRANCH, cwd=self.tree)
        )
        self.assertIn(f"家族 {PARENT} の控えが gone", refused.stderr)
        self.assertIn(f"ccnavi-sync.sh {PARENT}", refused.stderr)


if __name__ == "__main__":
    unittest.main()
