"""フィードバック計画の順序の受入テスト。

フィードバック計画も全体計画と同じく、ステップの `after` で先行関係が決まる。ここではターミナルの
承認で見る（先行関係の計算・承認の検査の細部・全体計画が終わったときの案内は
go の phases・phase のテスト）。

1. 並行する 2 ステップを最後のステップが受けない形は落ちる
2. 並行する 2 ステップの子は一緒に承認でき、合流のステップの子は両方が閉じるまで承認しない
3. 承認したフィードバック計画の after は変えられず、最初のフィードバック計画と一緒に
   全体計画は変えられない
"""

from __future__ import annotations

import os
import unittest

from tests.ticket.test_phases import PhaseHarness
from tests.ticketsample import child_text, parent_text


class FeedbackFlowTest(PhaseHarness):
    """1・2. ターミナルの承認で、フィードバック計画の並行と合流を通す。"""

    FB = [
        "implement-feedback",
        {"type": "implement-feedback", "after": []},
        {"type": "implement-feedback", "after": [2, 3]},
    ]

    def plan_reviewed(self, confirm=True):
        """全体計画（design 1 ステップ）を閉じてレビューを済ませる。

        `confirm` が偽なら依頼まで。
        """
        self.family(plan=["design"])
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01-01")
        fixture = self.remote()
        self.assertEqual(self.request(fixture, 1).returncode, 0)
        if confirm:
            self.assertEqual(self.confirm(fixture, 1).returncode, 0)
        self.start_parent()

    def is_approved(self, name):
        return os.path.exists(os.path.join(self.approved, "doing", name + ".md"))

    def test_a_feedback_plan_without_a_join_is_refused(self):
        self.plan_reviewed()
        fb = ["implement-feedback", {"type": "implement-feedback", "after": []}]
        self.propose("i0001", parent_text("i0001", ["design"], feedback=fb))
        self.commit_parent("feedback")
        refused = self.approve()
        self.assertNotEqual(refused.returncode, 0, refused.stdout + refused.stderr)
        self.assertIn("`feedback` の最後のステップ", refused.stderr)
        self.assertIn("最後に 1 ステップで受ける", refused.stderr)

    def test_parallel_feedback_children_start_together_and_the_join_waits(self):
        self.plan_reviewed()
        self.propose("i0001", parent_text("i0001", ["design"], feedback=self.FB))
        self.commit_parent("feedback")
        planned = self.approve()
        self.assertEqual(planned.returncode, 0, planned.stdout + planned.stderr)
        for n in (2, 3, 4):
            name = f"i0001-0{n}-0{n}"
            self.propose(name, child_text(name, "i0001", n, ["src/*"]))
        self.commit_parent("approval-request feedback children")
        result = self.approve()
        self.assertTrue(self.is_approved("i0001-02-02"), result.stdout + result.stderr)
        self.assertTrue(self.is_approved("i0001-03-03"), result.stdout + result.stderr)
        self.assertFalse(self.is_approved("i0001-04-04"))
        self.assertIn("閉じるまで承認しない", result.stderr)
        explained = self.ccnavi("--explain").stdout
        self.assertIn("フェーズ 3（実装フィードバック対応）", explained)
        self.assertIn("先行 1", explained)


class FeedbackRevisionTest(FeedbackFlowTest):
    """フィードバック計画を立てる改版と、同じ承認・あとの改版の扱い。"""

    def test_the_join_child_waits_even_when_approved_with_the_feedback_plan(self):
        """フィードバック計画を立てる改版と子を一緒に出しても、合流のステップの子は先行が閉じるまで承認されない。"""
        self.plan_reviewed()
        self.propose("i0001", parent_text("i0001", ["design"], feedback=self.FB))
        for n in (2, 3, 4):
            name = f"i0001-0{n}-0{n}"
            self.propose(name, child_text(name, "i0001", n, ["src/*"]))
        self.commit_parent("feedback and children")
        result = self.approve()
        self.assertTrue(self.is_approved("i0001-02-02"), result.stdout + result.stderr)
        self.assertTrue(self.is_approved("i0001-03-03"), result.stdout + result.stderr)
        self.assertFalse(self.is_approved("i0001-04-04"), result.stdout + result.stderr)
        self.assertIn("閉じるまで承認しない", result.stderr)

    def test_an_approved_feedback_plan_cannot_change_its_after(self):
        self.plan_reviewed()
        self.propose("i0001", parent_text("i0001", ["design"], feedback=self.FB))
        self.commit_parent("feedback")
        self.assertEqual(self.approve().returncode, 0)
        changed = [
            "implement-feedback",
            {"type": "implement-feedback", "after": [2]},
            {"type": "implement-feedback", "after": [3]},
        ]
        self.propose("i0001", parent_text("i0001", ["design"], feedback=changed))
        self.commit_parent("change feedback")
        refused = self.approve()
        self.assertNotEqual(refused.returncode, 0, refused.stdout + refused.stderr)
        self.assertIn("1 回だけ", refused.stderr)

    def test_the_plan_cannot_change_together_with_the_first_feedback_plan(self):
        self.plan_reviewed()
        plan = ["design", {"type": "design", "after": [1]}]
        self.propose("i0001", parent_text("i0001", plan, feedback=["implement-feedback"]))
        self.commit_parent("plan and feedback")
        refused = self.approve()
        self.assertNotEqual(refused.returncode, 0, refused.stdout + refused.stderr)
        self.assertIn("全体計画を変えられない", refused.stderr)

    def test_the_screen_says_which_feedback_items_start_together(self):
        self.plan_reviewed()
        self.propose("i0001", parent_text("i0001", ["design"], feedback=self.FB))
        self.commit_parent("feedback")
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("すぐ始まる（フィードバック計画）: 2, 3", result.stdout)
        self.assertIn("フィードバック計画を改版した", result.stdout)


if __name__ == "__main__":
    unittest.main()
