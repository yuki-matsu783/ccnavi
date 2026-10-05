"""再開（`done/` から `doing/` へ手で戻す）で残った `reviewed` のマーカーの警告。

運用の基本は、閉じたチケットを戻さず新しいチケットを作り直すこと。再開されると、そのフェーズの
`reviewed` が残る。機構はマーカーを消さず、`--lint` と `ticket status` が warn で言う。
見るのは次のとおり。

1. 作業中の子があり、レビューが要るフェーズに `reviewed` が残っていれば、lint と status が言う
2. 言わないもの: `reviewed` が無い、子が閉じている（`done/`）、レビューが要らないフェーズ
3. 警告は判定と `start` を止めない
"""

from __future__ import annotations

import os

from tests.ticket.test_phases import PhaseHarness, child_text
from tests.ticket.test_ticket import write

MARKER_WORDS = "reviewed マーカーが残っています"


class ResumedReviewWarnTest(PhaseHarness):
    def setup_child(self, plan, review, marker, start=True):
        """親と子 1 本を承認して着手し、必要ならフェーズ 1 に reviewed のマーカーを置く。"""
        self.family(plan=[plan])
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ("wip/*",), review))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.start_parent()
        self.worktree("i0001-01-01", "i0001")
        if start:
            started = self.ccnavi("ticket", "start", "i0001-01-01")
            self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        if marker:
            write(
                os.path.join(self.approved, "phases", "i0001", "1.reviewed"),
                '{"at": "2026-01-01T00:00:00+0000"}\n',
            )
        self.commit_parent()

    def status_of_child(self):
        out = self.ccnavi("ticket", "status", "i0001")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return out.stdout

    def lint(self):
        return self.ccnavi("--lint").stdout

    # ---- 1. 言う

    def test_a_doing_child_with_a_left_reviewed_marker_is_warned_by_lint_and_status(self):
        self.setup_child("design", True, marker=True)
        for text in (self.lint(), self.status_of_child()):
            self.assertIn(MARKER_WORDS, text)
            self.assertIn("フェーズ 1（設計）", text)
            self.assertIn("ccnavi-git.sh rm .ccnavi/approved/phases/i0001/1.reviewed", text)
            # マーカーのツリーを、ワークスペースルートからの相対で示す
            self.assertIn("ツリー .claude/worktrees/i0001で", text)
            # 取り込み済みの親子だけ push-approved が送る。それ以外はユーザがコミットする
            self.assertIn("取り込み済みで origin があり、chat だけでない親子なら", text)
            self.assertIn("ccnavi-push-approved.sh i0001", text)
            self.assertIn("ユーザが自分でコミットする", text)
            self.assertIn("新しいチケットを作り直す", text)
        # マーカーは消さない
        self.assertTrue(
            os.path.exists(os.path.join(self.approved, "phases", "i0001", "1.reviewed"))
        )

    # ---- 2. 言わない

    def test_no_warning_without_a_reviewed_marker(self):
        self.setup_child("design", True, marker=False)
        self.assertNotIn(MARKER_WORDS, self.lint())
        self.assertNotIn(MARKER_WORDS, self.status_of_child())

    def test_no_warning_for_a_closed_child(self):
        self.setup_child("design", True, marker=True)
        os.makedirs(os.path.join(self.approved, "done"), exist_ok=True)
        os.rename(
            os.path.join(self.approved, "doing", "i0001-01-01.md"),
            os.path.join(self.approved, "done", "i0001-01-01.md"),
        )
        self.commit_parent()
        self.assertNotIn(MARKER_WORDS, self.lint())
        self.assertNotIn(MARKER_WORDS, self.status_of_child())

    def test_no_warning_for_a_phase_that_needs_no_review(self):
        # 調査は review: none。マーカーが残っていても、止める条件に入らないので言わない。
        self.setup_child("research", False, marker=True)
        self.assertNotIn(MARKER_WORDS, self.lint())
        self.assertNotIn(MARKER_WORDS, self.status_of_child())

    # ---- 3. 止めない

    def test_the_warning_does_not_stop_start_or_the_verdict(self):
        self.setup_child("design", True, marker=True, start=False)
        started = self.ccnavi("ticket", "start", "i0001-01-01")
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        child = os.path.join(self.root, ".claude", "worktrees", "i0001-01-01")
        result = self.hook(
            "PreToolUse",
            "Write",
            child,
            file_path=os.path.join(child, "wip", "design", "x.md"),
        )
        self.assertNotIn("DENY", self.reason(result))
        # lint は warn だけで、終了コードを落とさない
        warned = self.ccnavi("--lint")
        self.assertIn(MARKER_WORDS, warned.stdout)
        self.assertEqual(warned.returncode, 0, warned.stdout)
        self.assertIn("error 0 件", warned.stdout)
