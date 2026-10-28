"""識別子と違う名前のワークツリーで親を承認すると、`ready` まで進んで止まる（issue #334）。

実装が終わってから気づくと手戻りが大きいので、早い段で止める。

1. 承認: 親（子）の提案が識別子と違う名前のワークツリーに在れば、承認しない（error）
2. 着手・完了: 承認済みチケットが識別子と違う名前のワークツリーに在れば、`start` `finish` が止まる
3. 案内: 止めた文面に、直す手順（同名のワークツリーを切り、提案を移し、承認を取り直す）が出る
"""

from __future__ import annotations

import os
import shutil

from ccnavi.tickets import approval_checks
from ccnavi.tickets.model import ticket_model
from tests.ticket.test_phases import PhaseHarness, child_text, git
from tests.ticketsample import parent_text


class MisplacedHomeTest(PhaseHarness):
    def stray_tree(self, name="skill-proposals"):
        return self.worktree(name, "main")

    def commit_in(self, tree, message):
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", message)

    def move_approved_to(self, tree):
        """承認済みチケットの置き場ごと、別のツリーへ動かす（別名のツリーで承認した形を作る）。"""
        shutil.move(os.path.join(self.parent_tree, ".ccnavi"), os.path.join(tree, ".ccnavi"))

    # ---- 1. 承認

    def test_a_parent_proposal_in_a_differently_named_worktree_is_not_approved(self):
        stray = self.stray_tree()
        self.propose("i0003", parent_text("i0003", ["research"]), tree=stray)
        self.commit_in(stray, "propose i0003")
        result = self.ccnavi("user", "agree", stdin="y\n")
        said = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, said)
        self.assertIn("提案 i0003 を識別子と違う名前のワークツリー skill-proposals", said)
        self.assertIn("ccnavi-worktree.sh i0003", said)
        self.assertIn("承認の前に直す", said)
        self.assertFalse(
            os.path.exists(os.path.join(stray, ".ccnavi", "approved", "doing", "i0003.md"))
        )

    def test_a_parent_proposal_in_its_own_worktree_is_approved(self):
        own = self.worktree("i0003", "main")
        self.propose("i0003", parent_text("i0003", ["research"]), tree=own)
        self.commit_in(own, "propose i0003")
        result = self.ccnavi("user", "agree", stdin="y\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(
            os.path.exists(os.path.join(own, ".ccnavi", "approved", "doing", "i0003.md"))
        )

    def test_a_child_proposal_outside_the_parents_worktree_is_not_approved(self):
        self.family()
        stray = self.stray_tree()
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ("wip/design/*",)), tree=stray
        )
        self.commit_in(stray, "propose child")
        result = self.ccnavi("user", "agree", stdin="y\n")
        said = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, said)
        self.assertIn("子の提案 i0001-01-01 を識別子と違う名前のワークツリー skill-proposals", said)
        self.assertIn("ccnavi-worktree.sh i0001", said)

    def settings(self):
        from ccnavi.infra import settings

        return settings.Settings(tickets="wip/proposals")

    def test_a_proposal_in_the_workspace_root_is_not_stopped(self):
        t = ticket_model.Ticket(ticket="i0003", tree_root=self.root)
        self.assertEqual(approval_checks.place_problems(self.root, self.settings(), t), [])

    # ---- 2. 着手・完了

    def test_start_stops_when_the_approved_ticket_is_in_another_worktree(self):
        self.family()
        stray = self.stray_tree()
        self.move_approved_to(stray)
        started = self.ccnavi("ticket", "start", "i0001")
        said = started.stdout + started.stderr
        self.assertEqual(started.returncode, 1, said)
        self.assertIn("ワークツリーの外", said)
        self.assertIn("skill-proposals", said)

    def test_finish_stops_when_the_approved_ticket_is_in_another_worktree(self):
        self.family()
        started = self.ccnavi("ticket", "start", "i0001")
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        stray = self.stray_tree()
        self.move_approved_to(stray)
        finished = self.ccnavi("ticket", "finish", "i0001")
        said = finished.stdout + finished.stderr
        self.assertEqual(finished.returncode, 1, said)
        self.assertIn("ワークツリーの外", said)
        # 止めたので、閉じたチケットは別名のツリーの doing/ に残る（done/ へ動かない）
        self.assertTrue(
            os.path.exists(os.path.join(stray, ".ccnavi", "approved", "doing", "i0001.md"))
        )

    def test_start_passes_in_the_parent_worktree(self):
        self.family()
        started = self.ccnavi("ticket", "start", "i0001")
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)

    # ---- 3. 案内

    def test_the_guidance_names_the_way_out(self):
        text = approval_checks.misplaced_home_text(self.root, "i0001", "skill-proposals")
        self.assertIn("親 i0001 の承認済みチケットが親のワークツリー", text)
        self.assertIn("skill-proposals に在る", text)
        self.assertIn("ccnavi-worktree.sh i0001", text)
        self.assertIn("承認を取り直してもらう", text)
        self.assertIn("手で動かさない", text)
        self.assertIn("ccnavi-user-push-approved.sh と ccnavi-sync.sh", text)
