"""権威のツリーの外に残った提案の古い写し（承認済みの識別子）の受入テスト。

承認済みの識別子の提案は、承認済みチケットの写りと合わせて権威のツリー（親のツリー →
元ツリー → 決まらない）を決め、そのツリーに在るものだけを読む（`approval.scan_proposals`）。
見るのは次のとおり。

1. 改版の前に切ったワークツリーに残った古い版（場面 C）を、改版として承認の対象にしない
2. 権威のツリーで進めている改版（`todo/` と `doing/` が並ぶ）は、承認の対象に残る
3. 権威でないツリーに書いた改版は承認の対象にならず、`--lint` が書く場所を案内する
"""

from __future__ import annotations

import json
import os
import unittest

from tests.ticket.test_phases import PhaseHarness, parent_text
from tests.ticket.test_ticket import write


class StaleProposalTest(PhaseHarness):
    def pending(self):
        result = self.ccnavi("--explain", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)["pending_approval"]

    def lint_lines(self, ticket_id):
        lint = self.ccnavi("--lint", "--mode", "enable")
        return [line for line in lint.stdout.splitlines() if ticket_id in line]

    def test_an_old_revision_left_in_a_worktree_is_not_offered_again(self):
        """場面 C。改版 1 が todo/ に在る間に切ったワークツリーに、改版 1 が残る。

        改版 1 → 改版 2 と承認したあと、残った改版 1 を改版と読むと、承認待ちに入り、
        承認すると計画が巻き戻る。
        """
        self.family(plan=("research", "design"))
        self.propose("i0001", parent_text("i0001", ["research", "design", "chores"]))
        self.commit_parent()
        self.worktree("i0001-07", "i0001")
        self.assertEqual(self.approve().returncode, 0)
        self.propose("i0001", parent_text("i0001", ["research", "chores"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        stale = os.path.join(
            self.root, ".claude", "worktrees", "i0001-07", "wip", "proposals", "todo", "i0001.md"
        )
        self.assertTrue(os.path.isfile(stale))

        self.assertEqual(self.pending(), [])
        again = self.ccnavi("--agree", stdin="y\n")
        self.assertNotIn("design", again.stdout)
        with open(os.path.join(self.approved, "doing", "i0001.md"), encoding="utf-8") as f:
            held = f.read()
        self.assertNotIn("design", held)
        self.assertIn("chores", held)

        lines = self.lint_lines("i0001")
        self.assertTrue(
            any(
                "計画の違う提案" in line
                and ".claude/worktrees/i0001-07/wip/proposals/todo/i0001.md" in line
                and "i0001 の todo/ に書き" in line
                for line in lines
            ),
            lines,
        )

    def test_a_revision_in_the_home_tree_stays_waiting(self):
        """改版は権威のツリーの todo/ に置く（ADR-0055）。承認済みチケットと並んでも承認待ちに残る。

        古い写しを持つワークツリーがあっても、権威のツリーの改版は読む。
        """
        self.family(plan=("research", "design"))
        self.worktree("i0001-07", "i0001")
        self.propose("i0001", parent_text("i0001", ["research", "design", "chores"]))
        self.commit_parent()
        self.assertEqual(self.pending(), ["i0001"])
        self.assertEqual(self.lint_lines("古い"), [])
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with open(os.path.join(self.approved, "doing", "i0001.md"), encoding="utf-8") as f:
            self.assertIn("chores", f.read())

    def test_a_revision_written_outside_the_home_tree_is_named_with_where_to_write(self):
        """権威でないツリー（ここではワークスペースルート）に書いた改版は承認の対象にならない。

        何も言わずに落とさず、`--lint` が書く場所（権威のツリーの todo/）を案内する。
        """
        self.family(plan=("research", "design"))
        write(
            os.path.join(self.root, "wip", "proposals", "todo", "i0001.md"),
            parent_text("i0001", ["research", "design", "chores"]),
        )
        self.assertEqual(self.pending(), [])
        lines = self.lint_lines("i0001")
        self.assertTrue(
            any(
                "計画の違う提案" in line
                and "wip/proposals/todo/i0001.md" in line
                and "改版なら i0001 の todo/ に書き" in line
                for line in lines
            ),
            lines,
        )

    def test_a_new_proposal_in_a_worktree_is_still_waiting(self):
        """承認済みの無い識別子（新規の提案）は今までどおり。どのツリーに在っても承認待ち。"""
        self.family(plan=("research", "design"))
        other = self.worktree("i0001-07", "i0001")
        write(
            os.path.join(other, "wip", "proposals", "todo", "i0002.md"),
            parent_text("i0002", ["research"]),
        )
        self.assertEqual(self.pending(), ["i0002"])


if __name__ == "__main__":
    unittest.main()
