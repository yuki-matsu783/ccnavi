"""親の改版で範囲（allow / ask / deny）を変える規則の受入テスト。

見るのは 4 つ（判定と画面の細部は go/internal/tickets の approval・proposal/agree・
proposal/candidates が見る）。

1. 判定: 範囲だけが違う提案も改版の候補になる。書く順だけの違いは違いにしない
2. 錠: 範囲を変える改版は、作業中（doing/）の子が居ないときだけ通る。範囲が同じなら子を見ない
3. 書き込み: 改版は範囲を提案の値に差し替える。提案に無い節は外す
4. 画面: 範囲の差分（追加・削除）を deny / ask / allow ごとに出す
"""

from __future__ import annotations

import os
import unittest

from tests.ticket.test_phases import PhaseHarness, child_text
from tests.ticket.test_phases import parent_text as plan_parent_text


class ScopeRevisionFlowTest(PhaseHarness):
    """承認から取り消しまでを通した流れ。"""

    PLAN = ["research", "design"]
    WIDER = ("src/*", "wip/*", "tests/*", "docs/*")

    def doing(self, name):
        return os.path.join(self.approved, "doing", name + ".md")

    def read_doing(self, name):
        with open(self.doing(name), encoding="utf-8") as f:
            return f.read()

    def revise(self, allow):
        self.propose(
            "i0001", plan_parent_text("i0001", self.PLAN, allow=allow, copy=self.phases_text)
        )
        self.commit_parent("revise")
        return self.approve()

    def test_a_scope_only_revision_replaces_the_scope_and_shows_the_diff(self):
        self.family(plan=self.PLAN)
        result = self.revise(self.WIDER)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("親の改版", result.stdout)
        self.assertIn("■ 範囲の差分", result.stdout)
        self.assertIn("allow 追加: docs/*", result.stdout)
        self.assertIn("glob: docs/*", self.read_doing("i0001"))
        self.assertFalse(
            os.path.exists(os.path.join(self.parent_tree, "wip", "proposals", "todo", "i0001.md"))
        )

    def test_narrowing_shows_the_removed_entry(self):
        self.family(plan=self.PLAN)
        result = self.revise(("src/*", "wip/*"))
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("allow 削除: tests/*", result.stdout)

    def test_a_stale_copy_in_a_child_worktree_does_not_count_after_cancel(self):
        """取り消したあとも子のワークツリーには切り出した時点の doing/ のコピーが残る。"""
        self.family(plan=self.PLAN)
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/research/*"], review=False)
        )
        self.commit_parent("child")
        self.assertEqual(0, self.approve().returncode)
        worktree = self.run_child("i0001-01-01")
        self.assertTrue(
            os.path.exists(os.path.join(worktree, ".ccnavi", "approved", "doing", "i0001-01-01.md"))
        )
        cancelled = self.ccnavi("ticket", "cancel", "i0001-01-01", "--reason", "範囲を広げる")
        self.assertEqual(0, cancelled.returncode, cancelled.stdout + cancelled.stderr)
        # 子を個別に取り消したときは、次の一手（親の status）を言う
        self.assertIn("次: ", cancelled.stdout)
        self.commit_parent("cancel")
        result = self.revise(self.WIDER)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_a_child_in_progress_stops_the_revision_until_it_is_cancelled(self):
        self.family(plan=self.PLAN)
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/research/*"], review=False)
        )
        self.commit_parent("child")
        self.assertEqual(0, self.approve().returncode)
        stopped = self.revise(self.WIDER)
        self.assertNotEqual(0, stopped.returncode, stopped.stdout + stopped.stderr)
        self.assertIn("i0001-01-01", stopped.stdout + stopped.stderr)
        self.assertNotIn("docs/*", self.read_doing("i0001"))

        cancelled = self.ccnavi("ticket", "cancel", "i0001-01-01", "--reason", "範囲を広げる")
        self.assertEqual(0, cancelled.returncode, cancelled.stdout + cancelled.stderr)
        self.commit_parent("cancel")
        result = self.approve()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("docs/*", self.read_doing("i0001"))


if __name__ == "__main__":
    unittest.main()
