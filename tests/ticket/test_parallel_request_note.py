"""レビュー準備中・レビュー待ちの間の、新しい依頼についての案内。

案内だけで、セッション単位で機械的に止める仕組みではない。止める（サブエージェントの起動とシェル
実行）のは今までどおり、その親の cwd からだけ。案内は、止めたときの文（`phase.hold_reason`）と、
`request` が成功したときの出力に添える。
"""

from __future__ import annotations

import unittest

from ccnavi.tickets.phase import gates as phase
from tests.ticket.test_phases import PhaseHarness
from tests.ticketsample import child_text

PARTS = (
    "自分が準備・依頼したレビューの待ち中は、別の親・別の目的の新しい依頼を受けない。",
    "受けるときは、ワークスペースルートで起動した別のセッションで受けるようユーザに伝える。",
    "他の会話が待っている親は対象外。",
    "同じ親のレビュー対応・相談・止める指示・取り消し・次のフェーズの計画・動いている子の完了処理は、"
    "このセッションで続けてよい。",
)


class ParallelRequestNoteTest(PhaseHarness):
    def closed_phase(self):
        self.family(plan=["design"])
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01-01")

    def test_the_constant_says_it_is_a_guide_and_names_what_stays_allowed(self):
        for part in PARTS:
            self.assertIn(part, phase.PARALLEL_REQUEST_NOTE)

    def test_the_hold_text_has_the_note_before_and_after_the_request(self):
        self.closed_phase()
        # レビュー準備中（依頼の前）
        preparing = self.reason(
            self.hook("PreToolUse", "Agent", self.parent_tree, description="次")
        )
        self.assertIn("DENY_PHASE_REVIEW", preparing)
        for part in PARTS:
            self.assertIn(part, preparing)
        shell = self.reason(self.hook("PreToolUse", "Bash", self.parent_tree, command="make"))
        for part in PARTS:
            self.assertIn(part, shell)
        # レビュー待ち（依頼のあと）
        fixture = self.remote()
        asked = self.request(fixture, 1)
        self.assertEqual(asked.returncode, 0, asked.stderr)
        waiting = self.reason(self.hook("PreToolUse", "Agent", self.parent_tree, description="次"))
        for part in PARTS:
            self.assertIn(part, waiting)
        # request が成功したときの出力にも添える
        for part in PARTS:
            self.assertIn(part, asked.stdout)

    def test_the_note_does_not_stop_anything_by_itself(self):
        """案内だけ。親の cwd の外（ワークスペースルート）からの起動は止めない。"""
        self.closed_phase()
        outside = self.hook("PreToolUse", "Agent", self.root, description="別の親の依頼")
        self.assertNotIn("DENY_PHASE_REVIEW", self.reason(outside))


if __name__ == "__main__":
    unittest.main()
