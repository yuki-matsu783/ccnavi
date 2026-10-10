"""フィードバック計画の順序（設計 9.7）の受入テスト。

フィードバック計画も全体計画と同じく、項の `after` で待ち方が決まる。見るのは 4 つ。

1. 待ち方: フィードバック計画の項は全体計画の番号を全部と、フィードバック計画の中で `after` を
   推移的に辿った番号を待つ。`after` を書かない項はフィードバック計画の中では何も待たない
2. 承認の検査: 終端は 1 つ（最後の項がほかの全部を待つ）、最後の項は延期できない、延期の引き受け手は
   延期した項を待つ延期していない最小の番号。並行する 2 項を最後の項が受ける形は通り、
   受けない形は落ちる
3. 並行する 2 項の子は一緒に承認でき、合流の項の子は両方が閉じるまで承認しない
4. 全体計画が終わったときの案内に、合流の書き方と `phases:` の差し込みを言う
"""

from __future__ import annotations

import os
import unittest

from ccnavi.tickets import phase, ticket_model, workflow
from tests.ticket.test_phases import PhaseHarness, child_text, parent_text
from tests.ticket.test_phases_dag import item, parent_of

PLAN = [item("design"), item("docs", after=[1])]


class FeedbackWaitsTest(unittest.TestCase):
    """1・2. 待ち方と承認の検査（計算だけ）。"""

    def problems(self, feedback):
        return [
            p.detail for p in workflow.problems(parent_of(PLAN, feedback)) if p.severity == "error"
        ]

    def test_parallel_feedback_items_join_at_the_last_one(self):
        parent = parent_of(PLAN, [item("fixup"), item("fixup"), item("fixup", after=[3, 4])])
        wf = workflow.compute(parent)
        self.assertEqual(wf.waits[3], [1, 2])
        self.assertEqual(wf.waits[4], [1, 2])
        self.assertEqual(wf.waits[5], [1, 2, 3, 4])
        self.assertEqual(workflow.ready(parent, wf), [1, 3, 4])
        self.assertEqual(self.problems(parent.feedback), [])
        found = workflow.lines(parent, wf)
        self.assertIn("すぐ始まる（フィードバック計画）: 3, 4", found)

    def test_feedback_items_without_after_have_more_than_one_end(self):
        found = self.problems([item("fixup"), item("fixup")])
        self.assertTrue(any("`feedback` の最後の項" in d and "待たない" in d for d in found), found)
        self.assertTrue(any("最後に 1 項で受ける" in d for d in found), found)

    def test_a_straight_feedback_plan_still_passes(self):
        self.assertEqual(self.problems([item("fixup"), item("fixup", after=[3])]), [])

    def test_a_deferred_feedback_item_goes_to_the_join(self):
        parent = parent_of(
            PLAN, [item("fixup", "defer"), item("fixup"), item("fixup", after=[3, 4])]
        )
        parent.workflow = workflow.compute(parent)
        self.assertEqual(parent.workflow.review_at, {3: 5})
        self.assertEqual(parent.covered_by(5), [3])

    def test_a_deferred_feedback_item_without_a_taker_is_refused(self):
        found = self.problems([item("fixup", "defer"), item("fixup"), item("fixup", after=[4])])
        self.assertTrue(any("延期を引き受ける項が無い" in d for d in found), found)


class FeedbackFlowTest(PhaseHarness):
    """2・3. 端末の承認で、フィードバック計画の並行と合流を通す。"""

    FB = [
        "implement-feedback",
        {"type": "implement-feedback", "after": []},
        {"type": "implement-feedback", "after": [2, 3]},
    ]

    def plan_reviewed(self, confirm=True):
        """全体計画（design 1 項）を閉じてレビューを済ませる（`confirm` が偽なら依頼まで）。"""
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
        self.assertIn("`feedback` の最後の項", refused.stderr)
        self.assertIn("最後に 1 項で受ける", refused.stderr)

    def test_parallel_feedback_children_start_together_and_the_join_waits(self):
        self.plan_reviewed()
        self.propose("i0001", parent_text("i0001", ["design"], feedback=self.FB))
        self.commit_parent("feedback")
        planned = self.approve()
        self.assertEqual(planned.returncode, 0, planned.stdout + planned.stderr)
        for n in (2, 3, 4):
            name = f"i0001-0{n}-0{n}"
            self.propose(name, child_text(name, "i0001", n, ["src/*"]))
        self.commit_parent("propose feedback children")
        result = self.approve()
        self.assertTrue(self.is_approved("i0001-02-02"), result.stdout + result.stderr)
        self.assertTrue(self.is_approved("i0001-03-03"), result.stdout + result.stderr)
        self.assertFalse(self.is_approved("i0001-04-04"))
        self.assertIn("閉じるまで承認しない", result.stderr)
        explained = self.ccnavi("--explain").stdout
        self.assertIn("フェーズ 3（実装フィードバック対応）", explained)
        self.assertIn("待つ: 1", explained)


class FeedbackRevisionTest(FeedbackFlowTest):
    """フィードバック計画を立てる改版と、同じ承認・あとの改版の扱い。"""

    def test_the_join_child_waits_even_when_approved_with_the_feedback_plan(self):
        """フィードバック計画を立てる改版と子を一緒に出しても、合流の項の子は待つ。"""
        self.plan_reviewed(confirm=False)
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


class FeedbackHintTest(unittest.TestCase):
    """4. 全体計画が終わったときの案内。"""

    def test_the_hint_says_how_to_join_and_to_fill_phases(self):
        parent = ticket_model.Ticket(ticket="i0001", plan=[item("design")])
        text = phase._next_hint(parent, [], 1)
        self.assertIn("フィードバック計画", text)
        self.assertIn("after", text)
        self.assertIn("最後の項", text)
        self.assertIn("1 項で受ける", text)
        self.assertIn("--plan-order i0001 --fill-phases", text)


if __name__ == "__main__":
    unittest.main()
