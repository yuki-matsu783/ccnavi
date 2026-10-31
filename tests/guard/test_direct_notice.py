"""直接作業の案内 2 つの単体テスト。

- コミット後の案内（`hook.commitnote`）: メインの commit が成功したとき、直接作業の
  ワークツリーごとに 1 回だけ、レビュー手順の次の一手を足す。サブエージェント・dry-run・チケットに結び付く名前・
  コミットが積まれていない回は黙る
- SessionStart のワークツリー一覧（`tickets.notice.direct_worktrees`）: 直接作業だけを新しい順に
  最大 10 件言い、超えた分は本数を言う。チケットに結び付くもの・記録の無いものは入れない
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import time
import unittest

from ccnavi.hook import commitnote
from ccnavi.infra import hookio, settings
from ccnavi.tickets.notice import direct_worktrees
from tests import ROOT


def git(cwd, *args):
    done = subprocess.run(
        ["git", "-C", cwd, "-c", "user.email=a@b", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


class Workspace(unittest.TestCase):
    """ワークスペースのリポジトリ（main）と、直接作業のワークツリーを切る道具。"""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="ccnavi-direct-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = os.path.join(self.tmp, "ws")
        os.makedirs(self.root)
        git(self.root, "init", "--quiet", "-b", "main")
        write(os.path.join(self.root, "README"), "x\n")
        shutil.copytree(
            os.path.join(ROOT, ".ccnavi", "messages"),
            os.path.join(self.root, ".ccnavi", "messages"),
        )
        write(os.path.join(self.root, ".gitignore"), "/logs/\n/.ccnavi/\n/.claude/\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "--quiet", "-m", "init")
        self.conf, _ = settings.load(self.root)
        self.common = os.path.realpath(
            os.path.join(self.root, git(self.root, "rev-parse", "--git-common-dir"))
        )

    def worktree(self, name, record=True, age=0):
        """`.claude/worktrees/<名前>` を切り、直接作業の記録を置く（age 秒前のもの）。"""
        path = os.path.join(self.root, ".claude", "worktrees", name)
        git(self.root, "worktree", "add", "--quiet", "-b", name, path)
        if record:
            snap = git(self.root, "rev-parse", "main")
            text = f"version 4\nbase_branch main\nsnap_sha {snap}\n"
            stamp = os.path.join(self.common, "ccnavi-direct", name)
            write(stamp, text)
            then = time.time() - age
            os.utime(stamp, (then, then))
        return path

    def commit(self, path, text="y\n"):
        write(os.path.join(path, "work.txt"), text)
        git(path, "add", "work.txt")
        git(path, "commit", "--quiet", "-m", "work")

    def ticket(self, name):
        """同じ名前の提案を置いて、チケットに結び付ける。"""
        write(os.path.join(self.root, self.conf.tickets, "todo", f"{name}.md"), "---\n---\n")


class CommitNoteTest(Workspace):
    def say(self, cwd, *, command=None, agent_id="", session="s1"):
        sh = os.path.join(self.root, ".ccnavi", "scripts", "ccnavi-git.sh")
        payload = hookio.Input(
            event="PostToolUse",
            tool_name="Bash",
            tool_input={"command": command or f"sh {sh} commit -m x"},
            session_id=session,
            cwd=cwd,
            agent_id=agent_id,
        )
        return commitnote.notice(io.StringIO(), self.conf, self.root, payload)

    def test_says_the_review_step_once_per_worktree(self):
        path = self.worktree("fix-1-a")
        self.commit(path)
        first = self.say(path)
        self.assertIn("fix-1-a", first)
        self.assertIn("haiku", first)
        self.assertTrue(first.count("次:") == 1, first)
        self.commit(path, "z\n")
        self.assertEqual(self.say(path), "")

    def test_each_worktree_says_it_once(self):
        a = self.worktree("fix-1-a")
        b = self.worktree("fix-2-b")
        self.commit(a)
        self.commit(b)
        self.assertIn("fix-1-a", self.say(a))
        self.assertIn("fix-2-b", self.say(b))

    def test_follows_cd_in_the_command(self):
        path = self.worktree("fix-1-a")
        self.commit(path)
        sh = os.path.join(self.root, ".ccnavi", "scripts", "ccnavi-git.sh")
        self.assertIn("fix-1-a", self.say(self.root, command=f"cd {path} && sh {sh} commit -m x"))

    def test_silent_for_subagent_dry_run_and_ticket_bound(self):
        path = self.worktree("fix-1-a")
        self.commit(path)
        sh = os.path.join(self.root, ".ccnavi", "scripts", "ccnavi-git.sh")
        self.assertEqual(self.say(path, agent_id="sub1"), "")
        self.assertEqual(self.say(path, command=f"sh {sh} commit --dry-run"), "")
        self.assertEqual(self.say(path, command=f"sh {sh} status"), "")
        bound = self.worktree("fix-2-b")
        self.commit(bound)
        self.ticket("fix-2-b")
        self.assertEqual(self.say(bound), "")

    def test_silent_when_no_commit_was_made(self):
        path = self.worktree("fix-1-a")
        self.assertEqual(self.say(path), "")
        self.assertEqual(self.say(self.root), "")


class DirectWorktreesTest(Workspace):
    def text(self):
        return direct_worktrees.session_text(self.root, self.conf, time.monotonic() + 30)

    def test_empty_without_direct_worktrees(self):
        self.assertEqual(self.text(), "")
        self.worktree("no-record", record=False)
        self.assertEqual(self.text(), "")

    def test_lists_facts_newest_first_and_excludes_ticket_work(self):
        old = self.worktree("old-1-a", age=500)
        new = self.worktree("new-1-b", age=100)
        self.commit(new)
        write(os.path.join(old, "draft.txt"), "d\n")
        self.worktree("tick-1-c", age=300)
        self.ticket("tick-1-c")
        text = self.text()
        self.assertNotIn("tick-1-c", text)
        self.assertLess(text.index("new-1-b"), text.index("old-1-a"))
        new_line = next(ln for ln in text.splitlines() if "new-1-b" in ln)
        old_line = next(ln for ln in text.splitlines() if "old-1-a" in ln)
        self.assertIn("未コミット なし、起点から 1 コミット、integrate の記録 なし", new_line)
        self.assertIn("未コミット あり、起点から 0 コミット", old_line)
        self.assertIn("他のセッションの作業の可能性があります", text)
        self.assertNotIn("ほかに", text)

    def test_caps_at_ten_and_counts_the_rest(self):
        for i in range(12):
            self.worktree(f"w-{i:02d}-x", age=i)
        text = self.text()
        self.assertEqual(sum(1 for ln in text.splitlines() if ln.startswith("  - ")), 10)
        self.assertIn("ほかに 2 本", text)
        self.assertIn("全 12 本", text)
        self.assertIn("w-00-x", text)
        self.assertNotIn("w-11-x", text)

    def test_integrate_record_is_stated(self):
        self.worktree("rec-1-a")
        os.makedirs(os.path.join(self.common, "ccnavi-integrate", "rec-1-a"))
        self.assertIn("integrate の記録 あり（取り込みの途中）", self.text())
