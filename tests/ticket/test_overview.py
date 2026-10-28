"""このPCで作業中の親の一覧（`tickets/overview.py`）。

見るのは次のとおり。

1. `ticket status`（引数なし）が親を 1 行ずつ出す。親・ブランチ・MR・次の子・未送信
2. MR の番号は親チケットの `mr:`。取り込みの時刻（最終取り込み）は出さない
3. 親を渡した `ticket status` は、子のワークツリーの状態を出さない（引数なしのときだけ）
4. 子のワークツリーの状態（合流済み・未合流・未コミットあり）
5. セッション開始で渡す一覧（`overview.session_text`）
"""

from __future__ import annotations

import os

from ccnavi.infra import settings
from ccnavi.tickets import overview
from ccnavi.tickets.approval import store as approval
from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import git, write
from tests.ticketsample import child_text


class OverviewTest(PhaseHarness):
    def said(self, *args):
        result = self.ccnavi("ticket", "status", *args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def family_with_child(self):
        """親と、フェーズ 1 の子 1 つを承認する。"""
        self.family(plan=["research", "design"])
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/research/*"]))
        self.commit_parent()
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stderr)

    def parent_line(self, out):
        return next(x for x in out.splitlines() if x.startswith("- 親 i0001 "))

    # ---- 1・2. 一覧の 1 行

    def test_status_without_argument_lists_each_parent_in_one_line(self):
        self.family_with_child()
        out = self.said()
        self.assertIn("== このPCで作業中の親", out)
        line = self.parent_line(out)
        self.assertIn("ブランチ i0001", line)
        self.assertIn("MR !1", line)  # 親チケットの mr:
        self.assertIn("次の子 i0001-01-01", line)
        self.assertNotIn("最終取り込み", line)
        # origin が無いので、リモート追跡の ref が無い。送っていないものは「一度も送っていない」
        self.assertIn("未送信 一度も送っていないあり", line)

    def test_the_mr_number_comes_from_the_parent_ticket_only(self):
        self.family(plan=["research", "design"])
        line = self.parent_line(self.said())
        self.assertIn("MR !1", line)
        self.assertNotIn("リモート未確認", line)

    def test_row_reads_the_mr_from_the_parent_ticket(self):
        self.family(plan=["research"])
        rows = self.rows()
        self.assertEqual(rows[0].mr, "1")
        self.assertFalse(hasattr(rows[0], "fetched_at"))

    def rows(self):
        conf, _ = settings.load(self.root)
        raw = approval.read_raw(conf, self.root)
        doing, _ = approval.scan(conf, self.root, raw=raw)
        closed, _ = approval.scan(conf, self.root, closed=True, raw=raw)
        review, _ = approval.scan_review(conf, self.root, raw=raw)
        proposals, _ = approval.scan_proposals(conf, self.root, raw.everything)
        return overview.collect(conf, self.root, doing, review, closed, proposals)

    def test_unsent_commits_are_counted_against_the_remote_tracking_ref(self):
        self.family(plan=["research"])
        self.remote()
        line = self.parent_line(self.said())
        self.assertIn("未送信 なし", line)
        write(os.path.join(self.parent_tree, "a.txt"), "a")
        git(self.parent_tree, "add", "a.txt")
        git(self.parent_tree, "commit", "--quiet", "-m", "local work")
        line = self.parent_line(self.said())
        self.assertIn("未送信 1 コミットあり", line)

    # ---- 3. 親を渡したとき

    def test_status_of_a_parent_does_not_say_the_children_or_the_last_sync(self):
        self.family(plan=["research", "design"])
        out = self.said("i0001")
        self.assertNotIn("最終取り込み", out)
        self.assertNotIn("== このPCで作業中の親", out)
        self.assertNotIn("子のワークツリー:", out)

    # ---- 4. 子のワークツリーの状態

    def test_child_worktrees_are_merged_unmerged_or_dirty(self):
        self.family_with_child()
        child = self.run_child("i0001-01-01", files=[("wip/research/new.md", "x\n")])
        out = self.said()
        self.assertIn("子のワークツリー: i0001-01-01  未合流", out)
        write(os.path.join(child, "wip", "research", "dirty.md"), "y\n")
        self.assertIn("子のワークツリー: i0001-01-01  未コミットあり", self.said())
        os.remove(os.path.join(child, "wip", "research", "dirty.md"))
        self.merge("i0001-01-01")
        self.assertIn("子のワークツリー: i0001-01-01  合流済み", self.said())

    # ---- 5. セッション開始

    def test_session_text_lists_the_parents_of_this_pc(self):
        conf, _ = settings.load(self.root)
        self.assertEqual(overview.session_text(conf, self.root), "")
        self.family(plan=["research"])
        text = overview.session_text(conf, self.root)
        self.assertTrue(text.startswith("[ccnavi] このPCで作業中の親:"), text)
        self.assertIn("- 親 i0001  ブランチ i0001  MR !1", text)
        self.assertNotIn("最終取り込み", text)
        self.assertNotIn("リモート未確認", text)
