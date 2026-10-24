"""SessionStart で、終わっていない `integrate` の記録を知らせる（pending_integrate）の受入テスト。

記録（取り込み先のツリーの `.git/ccnavi-integrate/<名前>/marker`）の版 4 の続きの一手と、
版を知らない壊れた記録を、モデルの文脈と `systemMessage` の両方に出す。記録が無ければ何も足さない。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest

from ccnavi.infra import gitcmd, settings
from ccnavi.tickets import pending_integrate
from tests.ticket.test_ticket import TicketTest, git, write


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for name in sorted(vars(PendingIntegrateTest)):
        if name.startswith("test_"):
            suite.addTest(PendingIntegrateTest(name))
    return suite


HEAD = "終わっていない integrate の記録があります"


class PendingIntegrateTest(TicketTest):
    def marker(self, name, **fields):
        gitdir = os.path.join(self.root, ".git", "ccnavi-integrate", name)
        os.makedirs(gitdir, exist_ok=True)
        write(
            os.path.join(gitdir, "marker"),
            "".join(f"{k} {v}\n" for k, v in fields.items()),
        )

    def notice(self, deadline=None):
        conf = settings.Settings(projects="")
        return pending_integrate.pending_notice(
            self.root, conf, time.monotonic() + 30 if deadline is None else deadline
        )

    def head(self):
        return git(self.root, "rev-parse", "HEAD").strip()

    def start(self, **extra):
        payload = {
            "hook_event_name": "SessionStart",
            "source": "startup",
            "session_id": "ss",
            "cwd": self.root,
        }
        payload.update(extra)
        return self.ccnavi("--mode", "enable", stdin=json.dumps(payload))

    def test_no_record_says_nothing(self):
        self.assertEqual(self.notice(), "")
        self.assertNotIn(HEAD, self.start().stdout)

    def test_a_marker_that_is_not_version_4_is_told_as_broken_without_raising(self):
        head = self.head()
        self.marker("w4", state="staged", branch="w4", branch_sha=head, base=head)
        text = self.notice()
        self.assertIn(HEAD, text)
        self.assertIn("w4（取り込み先のツリー", text)
        self.assertIn("を知りません", text)
        self.assertIn("操作せず", text)
        self.assertIn("git reset --merge", text)
        self.assertIn("ccnavi-integrate/w4", text)
        self.assertNotIn("integrate-done", text)

    def test_a_marker_with_another_version_is_told_as_broken(self):
        self.marker("w5", version="3", head=self.head())
        text = self.notice()
        self.assertIn("記録の版（3）を知りません", text)
        self.assertIn("操作せず", text)

    def test_a_directory_without_a_marker_is_told_as_broken(self):
        os.makedirs(os.path.join(self.root, ".git", "ccnavi-integrate", "w9"))
        text = self.notice()
        self.assertIn("w9（取り込み先のツリー", text)
        self.assertIn("を知りません", text)

    def test_an_expired_deadline_starts_no_git_and_says_so(self):
        head = self.head()
        self.marker("w6", state="staged", branch="w6", branch_sha=head, base=head, tree="t")
        calls = []
        real = gitcmd.run
        gitcmd.run = lambda *a, **k: calls.append(a) or real(*a, **k)
        try:
            text = self.notice(deadline=time.monotonic() - 1)
        finally:
            gitcmd.run = real
        self.assertEqual(calls, [])
        self.assertIn("ほかのツリーは時間切れで確かめていません", text)

    def test_session_start_gives_both_context_and_system_message_before_docs(self):
        head = self.head()
        self.marker("w7", state="conflict", branch="w7", branch_sha=head, base=head)
        out = json.loads(self.start().stdout)
        self.assertIn(HEAD, out["hookSpecificOutput"]["additionalContext"])
        self.assertIn(HEAD, out["systemMessage"])

    def test_the_notice_does_not_write_to_the_object_database(self):
        head = self.head()
        tree = git(self.root, "rev-parse", "HEAD^{tree}").strip()
        self.marker("w10", state="staged", branch="w10", branch_sha=head, base=head, tree=tree)

        def objects():
            found = []
            for base, _dirs, files in os.walk(os.path.join(self.root, ".git", "objects")):
                found.extend(os.path.join(base, f) for f in files)
            return sorted(found)

        before = objects()
        self.assertIn(HEAD, self.notice())
        self.assertEqual(before, objects())

    # ---- 版 4

    def direct_record(self, name, base="main"):
        common = os.path.join(self.root, ".git")
        write(
            os.path.join(common, "ccnavi-direct", name),
            f"version 4\nbase_branch {base}\nsnap_sha {self.head()}\ncommon_dir {common}\n",
        )

    def make_k(self):
        """HEAD を親に、木を変えたコミット K を作る（ブランチは動かさない）。"""
        head = self.head()
        tree = git(self.root, "rev-parse", "HEAD^{tree}").strip()
        k = git(self.root, "commit-tree", tree, "-p", head, "-m", "K").strip()
        return head, tree, k

    def test_a_v4_marker_without_k_waits_for_confirmation(self):
        head = self.head()
        tree = git(self.root, "rev-parse", "HEAD^{tree}").strip()
        self.direct_record("v1")
        self.marker("v1", version="4", head=head, tip=head, c_tree=tree, tmp="/x/tmp", empty="no")
        text = self.notice()
        self.assertIn("確認を待っています", text)
        self.assertIn("integrate-done v1 -m", text)
        self.assertIn("integrate --abort v1", text)

    def test_a_v4_marker_whose_head_moved_asks_to_abort_and_redo(self):
        head = self.head()
        tree = git(self.root, "rev-parse", "HEAD^{tree}").strip()
        self.direct_record("v2")
        self.marker("v2", version="4", head="0" * 40, tip=head, c_tree=tree)
        text = self.notice()
        self.assertIn("取り込み先が動きました", text)
        self.assertIn("integrate --abort v2", text)

    def test_k_not_on_the_base_branch_resumes_from_integrate_done(self):
        head, tree, k = self.make_k()
        self.direct_record("v3")
        self.marker("v3", version="4", head=head, tip=head, c_tree=tree, k=k)
        text = self.notice()
        self.assertIn("まだ載っていません", text)
        self.assertIn("integrate-done v3", text)

    def test_k_on_the_base_branch_leaves_only_the_cleanup(self):
        head, tree, k = self.make_k()
        git(self.root, "merge", "--ff-only", "-q", k)
        self.direct_record("v4")
        self.marker("v4", version="4", head=head, tip=head, c_tree=tree, k=k)
        text = self.notice()
        self.assertIn("取り込み先に載っています", text)
        self.assertIn("integrate-done v4", text)
        self.assertNotIn("-m", text.split("integrate-done v4", 1)[1].split("`", 1)[0])

    def test_a_k_with_a_different_parent_or_tree_is_not_adopted(self):
        head, tree, k = self.make_k()
        self.direct_record("v5")
        self.marker("v5", version="4", head="0" * 40, tip=head, c_tree=tree, k=k)
        text = self.notice()
        self.assertIn("K の親か木が記録と違います", text)
        self.assertIn("操作せず", text)
        self.marker("v5", version="4", head=head, tip=head, c_tree="1" * 40, k=k)
        self.assertIn("K の親か木が記録と違います", self.notice())

    def test_a_v4_marker_missing_fields_is_told_as_broken(self):
        self.marker("v6", version="4", head=self.head())
        text = self.notice()
        self.assertIn("tip・c_tree がありません", text)
        self.assertIn("操作せず", text)

    def test_leftovers_without_a_marker_are_listed(self):
        tmp = os.path.join(self.root, "logs", "direct-tmp", "self", "left1")
        os.makedirs(tmp)
        git(self.root, "update-ref", "refs/ccnavi/direct/left2/c", self.head())
        text = self.notice()
        self.assertIn("left1", text)
        self.assertIn("一時ワークツリー", text)
        self.assertIn("left2", text)
        self.assertIn("refs/ccnavi/direct/left2/c", text)

    def test_a_done_record_with_things_left_is_listed_but_a_clean_one_is_not(self):
        done = os.path.join(self.root, ".git", "ccnavi-integrate-done")
        write(os.path.join(done, "d1"), "version 4\nk x\n")
        git(self.root, "branch", "d1")
        text = self.notice()
        self.assertIn("片付けの途中の done の記録（残り: ブランチ）", text)
        git(self.root, "branch", "-D", "d1")
        self.assertEqual(self.notice(), "")

    def test_a_working_state_is_not_listed(self):
        """作業中の記録・s の ref・作業ワークツリーは、普通の状態なので知らせない。"""
        self.direct_record("w1")
        git(self.root, "update-ref", "refs/ccnavi/direct/w1/s", self.head())
        self.assertEqual(self.notice(), "")

    def test_an_unused_pending_candidate_is_listed(self):
        git(self.root, "update-ref", "refs/ccnavi/direct/p1/pending", self.head())
        self.assertIn("使われなかった書きかけの候補", self.notice())

    def test_a_subagent_is_not_told(self):
        head = self.head()
        self.marker("w8", state="conflict", branch="w8", branch_sha=head, base=head)
        self.assertNotIn(HEAD, self.start(agent_id="a1").stdout)

    def test_the_commands_run_from_a_fresh_shell_in_a_path_with_space_and_quote(self):
        base = tempfile.mkdtemp(prefix="ccnavi-pend-")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        ws = os.path.join(base, "a b's")
        os.makedirs(os.path.join(ws, ".ccnavi", "scripts"))
        write(os.path.join(ws, ".ccnavi", "scripts", "ccnavi-git.sh"), 'echo "ARGS:$*"\n')
        line = pending_integrate._unknown_line(ws, ws, "nm", "")
        for cmd in re.findall(r"`([^`]+)`", line):
            cmd = cmd.replace("<メッセージ>", "msg")
            done = subprocess.run(["sh", "-c", cmd], cwd="/", capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, (cmd, done.stderr))
            self.assertIn("ARGS:", done.stdout)


if __name__ == "__main__":
    unittest.main()
