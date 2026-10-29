"""SubagentStart が、親の局面を 1 行で名指しする（#207、REQ-TKT-35 の後半）。

子の行（`  <子>（親 X、フェーズ N: タイトル）: ...`）のあとに、親の局面を
`親 <X> の局面: <phase.stage の文字列そのまま>` の形で 1 行足す。

1. 親のツリーからの起動と、子のツリーからの起動で、同じ行が出る
2. 並行する枝が止まる局面と、レビュー準備中・レビュー待ちは、実際の状態から求めた局面が出る
3. 計画の無い親には出さない
4. 親が複数の子を持っても、行は 1 回だけ
5. 局面の各文字（クローズ可・フィードバック対応中など）は、`phase.stage` が返したとおりに出る。
   これらは開いている子が居ない局面なので、実際の状態を作れない。`phase.stage` を差し替えて
   「そのまま出す」ことだけを見る（実装は `phase.stage` をモジュールの属性として呼ぶこと）
6. 局面を組む例外で SubagentStart を失敗させない

実装の前に書く受入テスト。実装が入るまでは、局面の行が無いために落ちる。
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from ccnavi.tickets.approval import marks as approval_marks
from ccnavi.tickets.phase import gates as phase
from tests.ticket.test_phases import PhaseHarness
from tests.ticketsample import child_text, parent_text

PARENT = "i0001"
CHILD1 = "i0001-01-01"
CHILD2 = "i0001-02-02"
LINE = f"親 {PARENT} の局面: "
# 調査と設計は並行し、実装に両方が先行する（終端は 1 つ）。
PARALLEL = [
    {"type": "research", "after": []},
    {"type": "design", "after": []},
    {"type": "implement", "after": [1, 2]},
]
RESEARCH_ALLOW = ("wip/research/*",)
DESIGN_ALLOW = ("wip/design/*",)

# 実装の局面の名前（変えない。docs が実装に合わせる）。
STAGES = [
    "作業中（2（実装とテスト））",
    "フィードバック対応中（3（実装フィードバック対応））",
    "レビュー準備中（1（設計））",
    "レビュー待ち（1（設計））",
    "フィードバック計画待ち",
    "クローズ可",
    "クローズ可（フィードバック対応済み）",
    "閉じられる（ユーザが早めにクローズした）",
]


class StageHarness(PhaseHarness):
    """親 1 本と子を承認して着手する道具。テストは持たない。"""

    def open_family(self, plan, children):
        """親と子を承認する。`children` は (識別子, フェーズ, 範囲) の並び。着手はしない。"""
        self.propose(PARENT, parent_text(PARENT, plan, copy=self.phases_text))
        for name, number, allow in children:
            self.propose(name, child_text(name, PARENT, number, allow))
        self.commit_parent()
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def start_text(self, cwd):
        """cwd から SubagentStart を起こし、渡された文（additionalContext）を返す。"""
        result = self.hook("SubagentStart", "", cwd, agent_id="sub-1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return self.reason(result)

    def board_stage(self):
        """ボードが出す親の局面（`phase.stage` と同じ計算）。期待値の元にする。"""
        board = self.ccnavi("--explain", "--json")
        self.assertEqual(board.returncode, 0, board.stderr)
        return {p["ticket"]: p for p in json.loads(board.stdout)["parents"]}[PARENT]["stage"]

    def assert_line_after_child(self, text, child, stage):
        """局面の行が出ていて、子の行より後ろにある。"""
        self.assertIn(LINE + stage, text)
        self.assertGreater(text.index(LINE + stage), text.index(child))


class StageLineTest(StageHarness):
    def test_the_parent_tree_names_the_stage_after_the_child_line(self):
        self.open_family(["research", "design"], [(CHILD1, 1, RESEARCH_ALLOW)])
        self.run_child(CHILD1)
        stage = self.board_stage()
        self.assertTrue(stage.startswith("作業中"), stage)
        text = self.start_text(self.parent_tree)
        self.assert_line_after_child(text, CHILD1, stage)

    def test_a_child_tree_names_the_stage_of_its_own_parent(self):
        self.open_family(["research", "design"], [(CHILD1, 1, RESEARCH_ALLOW)])
        child_tree = self.run_child(CHILD1)
        stage = self.board_stage()
        text = self.start_text(child_tree)
        self.assert_line_after_child(text, CHILD1, stage)

    def test_parallel_branches_are_listed_as_phase_stage_gives_them(self):
        """並行する枝は、`phase.stage` の文字列（番号を並べた形）がそのまま出る。"""
        self.open_family(PARALLEL, [(CHILD1, 1, RESEARCH_ALLOW), (CHILD2, 2, DESIGN_ALLOW)])
        self.run_child(CHILD1)
        self.run_child(CHILD2)
        stage = self.board_stage()
        self.assertTrue(stage.startswith("作業中"), stage)
        self.assertIn("1", stage)
        self.assertIn("2", stage)
        text = self.start_text(self.parent_tree)
        self.assert_line_after_child(text, CHILD1, stage)

    def test_the_line_comes_once_even_when_the_parent_has_several_children(self):
        self.open_family(PARALLEL, [(CHILD1, 1, RESEARCH_ALLOW), (CHILD2, 2, DESIGN_ALLOW)])
        self.run_child(CHILD1)
        self.run_child(CHILD2)
        text = self.start_text(self.parent_tree)
        self.assertIn(CHILD1, text)
        self.assertIn(CHILD2, text)
        self.assertEqual(text.count("の局面: "), 1, text)


class StageReviewTest(StageHarness):
    """止まっている枝がある局面。もう片方の枝の子が開いている間に、SubagentStart を起こす。"""

    def setUp(self):
        super().setUp()
        self.open_family(PARALLEL, [(CHILD1, 1, RESEARCH_ALLOW), (CHILD2, 2, DESIGN_ALLOW)])
        self.run_child(CHILD1)
        self.run_child(CHILD2, [("wip/design/a.md", "設計\n")])
        # 設計（mr）の子を閉じ、レビューが要る枝を止める。研究の子は開いたまま。
        closed = self.close_child(CHILD2)
        self.assertEqual(closed.returncode, 0, closed.stdout + closed.stderr)
        self.commit_parent("close 02")
        self.merge(CHILD2)

    def test_a_phase_waiting_for_its_review_to_be_prepared(self):
        stage = self.board_stage()
        self.assertIn("レビュー準備中", stage)
        text = self.start_text(self.parent_tree)
        self.assert_line_after_child(text, CHILD1, stage)

    def test_a_phase_whose_review_was_requested_is_waiting(self):
        failed = approval_marks.write_mark(
            self.approved, PARENT, 2, approval_marks.MARK_REQUESTED, {}
        )
        self.assertEqual(failed, "")
        stage = self.board_stage()
        self.assertIn("レビュー待ち", stage)
        text = self.start_text(self.parent_tree)
        self.assert_line_after_child(text, CHILD1, stage)


class StageWordingTest(StageHarness):
    """局面の各文字は、`phase.stage` が返したとおりに出す（言い換えない・足さない）。"""

    def setUp(self):
        super().setUp()
        self.open_family(["research", "design"], [(CHILD1, 1, RESEARCH_ALLOW)])
        self.child_tree = self.run_child(CHILD1)

    def test_every_stage_text_is_passed_through(self):
        for stage in STAGES:
            with self.subTest(stage=stage), mock.patch.object(phase, "stage", return_value=stage):
                text = self.start_text(self.parent_tree)
                self.assertIn(LINE + stage, text)

    def test_a_closable_stage_is_not_rewritten_as_a_waiting_for_the_whole_plan(self):
        """「全体計画待ち」は SubagentStart では起きない。どの局面でも出さない。"""
        for stage in STAGES:
            with self.subTest(stage=stage), mock.patch.object(phase, "stage", return_value=stage):
                self.assertNotIn("全体計画待ち", self.start_text(self.parent_tree))
        self.assertNotIn("全体計画待ち", self.start_text(self.child_tree))

    def test_an_empty_stage_leaves_no_line(self):
        with mock.patch.object(phase, "stage", return_value=""):
            text = self.start_text(self.parent_tree)
        self.assertIn(CHILD1, text)
        self.assertNotIn("の局面", text)

    def test_a_failing_stage_does_not_fail_the_subagent_start(self):
        """局面を組めなくても（読めない置き場など）、子の一覧と範囲は渡り、hook は成功する。"""
        for error in (OSError("読めない"), ValueError("崩れている"), KeyError("x")):
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(phase, "stage", side_effect=error):
                    result = self.hook("SubagentStart", "", self.parent_tree, agent_id="sub-1")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn("internal-error", result.stderr)
                text = self.reason(result)
                self.assertIn(CHILD1, text)
                self.assertIn("wip/research/*", text)
                self.assertNotIn(LINE, text)

    def test_the_stage_line_is_written_for_the_agent_only(self):
        """局面の行は subagent に渡る文脈にだけ入れ、ユーザ向けの systemMessage には足さない。"""
        with mock.patch.object(phase, "stage", return_value="クローズ可"):
            result = self.hook("SubagentStart", "", self.parent_tree, agent_id="sub-1")
        out = json.loads(result.stdout)
        self.assertNotIn("の局面", out.get("systemMessage", ""))


if __name__ == "__main__":
    unittest.main()
