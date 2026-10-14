"""このPCで作業中の親の一覧（`tickets/overview.py`）。

見るのは次のとおり。

1. `ticket status`（引数なし）が親を 1 行ずつ出す。親・ブランチ・MR・次の子・最終取り込み・未送信
2. 取り込み状態が無い親は「リモート未確認」。取り込み状態があれば時刻と MR の番号を出す
3. 親を渡した `ticket status` にも最終取り込みを出す。子のワークツリーの状態は引数なしのときだけ
4. 子のワークツリーの状態（合流済み・未合流・未コミットあり）
5. セッション開始で渡す一覧（`overview.session_text`）
"""

from __future__ import annotations

import os
from datetime import datetime

from ccnavi.infra import settings
from ccnavi.tickets import approval, overview
from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import git, write
from tests.ticketsample import child_text

FETCHED = 1_800_000_000


def label(seconds):
    return datetime.fromtimestamp(seconds).astimezone().strftime("%Y-%m-%d %H:%M")


class OverviewTest(PhaseHarness):
    def record(self, **fields):
        base = {
            "remote": "origin",
            "branch": "i0001",
            "sha": "0" * 40,
            "fetched_at": str(FETCHED),
            "state": "present",
            "reason": "",
        }
        base.update(fields)
        text = "".join(f"{k} {v}\n" for k, v in base.items())
        write(os.path.join(self.state, "sync", "self", "families", "i0001"), text)

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
        self.assertIn("最終取り込み リモート未確認", line)
        # origin が無いので、リモート追跡の ref が無い。送っていないものは「一度も送っていない」
        self.assertIn("未送信 一度も送っていないあり", line)

    def test_a_predecessor_of_a_family_closed_by_the_sync_is_met_as_in_the_approval(self):
        """閉じて片付いた別の親子の先行は、承認と同じく取り込み状態で閉じたと読む。"""
        write(
            os.path.join(self.state, "sync", "self", "families", "i0099"),
            "remote origin\nbranch i0099\nsha x\nfetched_at 1\nstate closed\nreason r\n",
        )
        write(
            os.path.join(self.state, "sync", "self", "integration", "head"),
            "branch main\nsha x\nsource default\n",
        )
        self.family(plan=["research", "design"])
        text = child_text("i0001-01-01", "i0001", 1, ["wip/research/*"])
        self.propose(
            "i0001-01-01", text.replace("phase: 1\n", "phase: 1\npredecessors: [i0099-01-01]\n")
        )
        self.commit_parent()
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("次の子 i0001-01-01", self.parent_line(self.said()))

    def test_record_gives_the_time_and_the_mr_number(self):
        self.family(plan=["research", "design"])
        self.record(mr="12")
        line = self.parent_line(self.said())
        self.assertIn(f"最終取り込み {label(FETCHED)}", line)
        # 親チケットの mr: が先。取り込み記録の mr（12）は親チケットに無いときの控え
        self.assertIn("MR !1", line)
        self.assertNotIn("リモート未確認", line)

    def test_row_reads_the_mr_from_the_record(self):
        self.family(plan=["research"])
        self.record(mr="12")
        rows = self.rows()
        self.assertEqual(rows[0].mr, "1")

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

    def test_status_of_a_parent_says_the_last_sync_but_not_the_children(self):
        self.family(plan=["research", "design"])
        out = self.said("i0001")
        self.assertIn("最終取り込み: リモート未確認", out)
        self.assertNotIn("== このPCで作業中の親", out)
        self.assertNotIn("子のワークツリー:", out)
        self.record()
        self.assertIn(f"最終取り込み: {label(FETCHED)}", self.said("i0001"))

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
        self.record(mr="3")
        text = overview.session_text(conf, self.root)
        self.assertTrue(text.startswith("[ccnavi] このPCで作業中の親:"), text)
        self.assertIn("- 親 i0001  ブランチ i0001  MR !1", text)
        self.assertIn(f"最終取り込み {label(FETCHED)}", text)
        self.assertNotIn("リモート未確認の親は", text)

    def test_session_text_points_to_sync_for_unchecked_parents(self):
        conf, _ = settings.load(self.root)
        self.family(plan=["research"])
        text = overview.session_text(conf, self.root)
        self.assertIn("リモート未確認", text)
        self.assertIn("ccnavi-sync.sh <親>", text)
