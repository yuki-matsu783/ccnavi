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

from ccnavi.tickets import approval
from ccnavi.tickets import ticket as ticket_mod
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
                and "改版なら .claude/worktrees/i0001 の wip/proposals/todo/ に書き" in line
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
                and "改版なら .claude/worktrees/i0001 の wip/proposals/todo/ に書き" in line
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

    def test_a_home_revision_copied_into_a_worktree_stays_waiting_and_is_not_named(self):
        """親のツリーの改版の写しが子のワークツリーにある形。承認待ちに残り、lint も言わない。"""
        self.family(plan=("research", "design"))
        self.propose("i0001", parent_text("i0001", ["research", "design", "chores"]))
        self.commit_parent()
        self.worktree("i0001-07", "i0001")  # 改版の todo/ を持ったまま切る
        self.assertEqual(self.pending(), ["i0001"])
        lines = self.lint_lines("i0001-07/wip/proposals/todo/i0001.md")
        self.assertEqual(lines, [])

    def test_the_approved_copies_alone_decide_the_home_tree(self):
        """承認済みチケットが元ツリー（ワークスペースルート）にしか無いとき、親の名前のワークツリーに
        残った承認前の `todo/` を権威と読まない（Chrome や他のセッションで承認され、手元の
        ワークツリーに届いていない形）。読むと古い版が改版として承認待ちに入り、巻き戻る。
        """
        self.family(plan=("research", "design"))
        held = os.path.join(self.approved, "doing", "i0001.md")
        with open(held, encoding="utf-8") as f:
            text = f.read()
        os.remove(held)
        write(os.path.join(self.root, ".ccnavi", "approved", "doing", "i0001.md"), text)
        self.propose("i0001", parent_text("i0001", ["research"]))  # 承認前の古い版
        self.commit_parent()

        self.assertEqual(self.pending(), [])
        lines = self.lint_lines("i0001")
        self.assertTrue(
            any(
                "計画の違う提案" in line
                and ".claude/worktrees/i0001/wip/proposals/todo/i0001.md" in line
                and "改版なら ワークスペースルート の wip/proposals/todo/ に書き" in line
                for line in lines
            ),
            lines,
        )

    def test_a_closed_identifier_proposed_in_another_tree_keeps_the_reopen_guidance(self):
        """閉じた識別子を別のツリーで提案したとき、`--lint` は前と同じ再開の案内を出す。"""
        self.family(plan=("research", "design"))
        write(
            os.path.join(self.root, ".ccnavi", "approved", "done", "i0005.md"),
            parent_text("i0005", ["research"]),
        )
        self.propose("i0005", parent_text("i0005", ["research", "design"]))
        self.commit_parent()
        lines = self.lint_lines("i0005")
        self.assertTrue(
            any(
                "i0005 は閉じたかレビュー待ちなのに todo/ にも在る" in line
                and "再開するには、ユーザが承認済みチケットを戻す" in line
                for line in lines
            ),
            lines,
        )
        self.assertEqual(self.pending(), [])

    def verify_names_where_to_write(self, place_rel):
        self.assertEqual(self.pending(), [])
        verify = self.ccnavi("--agree", "--preview", "--verify")
        self.assertNotEqual(verify.returncode, 0)
        self.assertIn("承認待ちに入らない改版がある", verify.stdout)
        self.assertIn(place_rel, verify.stdout)
        self.assertIn(
            "改版なら .claude/worktrees/i0001 の wip/proposals/todo/ に書き", verify.stdout
        )
        agree = self.ccnavi("--agree", stdin="y\n")
        self.assertIn("承認待ちのチケットは無い", agree.stdout)
        self.assertIn(place_rel, agree.stderr)
        self.assertIn(
            "改版なら .claude/worktrees/i0001 の wip/proposals/todo/ に書き", agree.stderr
        )

    def test_agree_names_a_revision_written_in_the_workspace_root(self):
        """権威でないツリー（ワークスペースルート）の改版は承認待ちに入れず、--agree と --verify が
        ファイルの場所と書くツリーを名指しする。"""
        self.family(plan=("research", "design"))
        write(
            os.path.join(self.root, "wip", "proposals", "todo", "i0001.md"),
            parent_text("i0001", ["research", "design", "chores"]),
        )
        self.verify_names_where_to_write("wip/proposals/todo/i0001.md")

    def test_agree_names_a_revision_written_in_a_child_worktree(self):
        """子のワークツリーに書いた改版も同じく名指しする。"""
        self.family(plan=("research", "design"))
        child = self.worktree("i0001-01", "i0001")
        write(
            os.path.join(child, "wip", "proposals", "todo", "i0001.md"),
            parent_text("i0001", ["research", "design", "chores"]),
        )
        self.verify_names_where_to_write(".claude/worktrees/i0001-01/wip/proposals/todo/i0001.md")


def _t(tree, state, project="", parent="", ticket="i0001"):
    return ticket_mod.Ticket(
        ticket=ticket, tree=tree, state=state, project=project, parent=parent, path=f"/{tree}"
    )


class AuthorityTest(unittest.TestCase):
    """権威のツリーの決め方（`ticket.authority`）。承認済みチケットと提案が同じ関数を通る。"""

    def test_approved_copies_alone_decide(self):
        approved = [_t("", "doing")]
        stale = [_t("i0001", ticket_mod.TODO)]
        self.assertEqual(ticket_mod.authority(approved), "")
        self.assertEqual(ticket_mod.fold(stale, approved), [])
        self.assertEqual([t.tree for t in approval._authoritative(approved, approved)], [""])

    def test_home_tree_wins_over_the_origin(self):
        approved = [_t("", "doing"), _t("i0001", "doing")]
        props = [_t("i0001", ticket_mod.TODO), _t("x", ticket_mod.TODO)]
        self.assertEqual(ticket_mod.authority(approved), "i0001")
        self.assertEqual([t.tree for t in ticket_mod.fold(props, approved)], ["i0001"])

    def test_a_cross_repository_collision_keeps_everything_as_before(self):
        """リポジトリをまたぐ衝突はまとめない（修正前と同じ答え）。"""
        approved = [_t("", "doing")]
        props = [
            _t("p1", ticket_mod.TODO, project="p1"),
            _t("p1-wt", ticket_mod.TODO, project="p1"),
        ]
        self.assertEqual(ticket_mod.fold(props, approved), props)
        self.assertEqual(ticket_mod.dedupe(props, approved), props)
        mixed = [_t("", "doing"), _t("p1", "doing", project="p1")]
        self.assertIsNone(ticket_mod.authority(mixed))
        self.assertEqual(approval._authoritative(mixed, mixed), mixed)

    def test_undecided_keeps_every_proposal(self):
        """親のツリーにも元ツリーにも承認済みチケットが無ければ、提案は全部残す（今までどおり）。"""
        approved = [_t("a", "doing"), _t("b", "done")]
        props = [_t("a", ticket_mod.TODO), _t("c", ticket_mod.TODO)]
        self.assertIsNone(ticket_mod.authority(approved))
        self.assertEqual(ticket_mod.fold(props, approved), props)


if __name__ == "__main__":
    unittest.main()
