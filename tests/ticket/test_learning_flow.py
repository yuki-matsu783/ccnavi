"""学びの蓄積の受入テスト（ticket start の案内と、ticket finish の検査）。

見るのは 5 つ。

1. 有効な子を着手すると、学びの蓄積の案内が出る
2. 有効化していなければ、着手の案内は出ない
3. 気づきの記録が無いまま子を閉じると、止めて learn を案内する
4. 気づきの記録は HEAD に在るものだけが通る（add だけでは通らない）
5. フェーズ定義が learning: off なら、記録が無くても閉じられ、着手の案内も出ない
"""

from __future__ import annotations

import os
import unittest

from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import git, write
from tests.ticketsample import child_text, parent_text

EN = {"CCNAVI_LEARNING": "enable"}
DIS = {"CCNAVI_LEARNING": "disable"}
PH = (
    "version: 1\nphases:\n  work:\n    kind: work\n    title: 作業\n"
    "    review: false\n    scope: inherit\n{extra}"
)
RECORD = "wip/handoff/learnings/i0001-01-01.md"
RECORD_TEXT = "---\nticket: i0001-01-01\nparent: i0001\nlearnings: none\n---\n\n"


class LearningFlowTest(PhaseHarness):
    def setup_family(self, extra="", env=EN):
        """親と子を承認し、子を着手する。返すのは子のワークツリーと着手の結果。"""
        text = PH.format(extra=extra)
        write(self.phases, text)
        scope = ("src/*", "wip/*")
        self.propose("i0001", parent_text("i0001", ["work"], allow=scope, copy=text))
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, list(scope), review=False)
        )
        self.commit_parent()
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        self.start_parent()
        tree = self.worktree("i0001-01-01", "i0001")
        started = self.ccnavi("ticket", "start", "i0001-01-01", env=env)
        return tree, started

    def fin(self):
        return self.ccnavi("ticket", "finish", "i0001-01-01", env=EN)

    def test_start_prints_learning_notice_when_enabled(self):
        _, started = self.setup_family()
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.assertIn("学びの蓄積", started.stdout)
        self.assertIn("git commit", started.stdout)

    def test_start_is_silent_when_disabled(self):
        _, started = self.setup_family(env=DIS)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.assertNotIn("学びの蓄積", started.stdout)

    def test_finish_refused_without_record(self):
        self.setup_family()
        refused = self.fin()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("気づきの記録", refused.stderr)
        self.assertIn("learn", refused.stderr)

    def test_finish_passes_after_committed_record(self):
        tree, _ = self.setup_family()
        write(os.path.join(tree, *RECORD.split("/")), RECORD_TEXT)
        git(tree, "add", "-f", "-A")
        # add だけでは HEAD に無いので通らない。
        staged_only = self.fin()
        self.assertNotEqual(staged_only.returncode, 0, staged_only.stdout + staged_only.stderr)
        git(tree, "commit", "--quiet", "-m", "rec")
        passed = self.fin()
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)

    def test_finish_exempt_when_phase_is_learning_off(self):
        _, started = self.setup_family("    learning: off\n")
        self.assertNotIn("学びの蓄積", started.stdout)
        closed = self.fin()
        self.assertEqual(closed.returncode, 0, closed.stdout + closed.stderr)


if __name__ == "__main__":
    unittest.main()
