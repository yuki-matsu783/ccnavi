"""`ccnavi ticket revise`（承認済みの親の改版の雛形）の受入テスト。

見るのは 5 つ。

1. `--feedback` は、承認済みチケットのコピーから、スクリプトだけが書く欄を外し、
   `feedback: []` を足す。書いた雛形は承認の確認（`--agree --preview --verify`）を通り、承認できる
2. 全体計画のレビューが済む前は、`--feedback` は何も書かずに止まる
3. `--feedback` を付けないと、写しだけを作る（`feedback:` は足さない）
4. `todo/` に同じ識別子の提案が既にあれば、上書きしない。承認済みの親が無い・
   cwd が親のワークツリーの外・子の識別子は、何も書かずに止まる
5. 親を閉じられない理由（`finish`）が、この雛形の作り方を言う
"""

from __future__ import annotations

import os

from ccnavi.tickets.model import ticket_model
from tests.ticket.test_feedback_plan_approval import FeedbackPlanApprovalTest
from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import git


class ReviseTest(PhaseHarness):
    # 全体計画のレビューを依頼したところまでの流れは、承認の試験の部品を借りる（継承すると
    # 承認の試験が丸ごともう 1 度走る）
    requested = FeedbackPlanApprovalTest.requested

    def revise(self, *extra, ident="i0001", cwd=None):
        return self.ccnavi("ticket", "revise", ident, *extra, "--cwd", cwd or self.parent_tree)

    def todo_path(self):
        return os.path.join(self.parent_tree, "wip", "proposals", "todo", "i0001.md")

    def reviewed(self):
        fixture = self.requested()
        self.assertEqual(self.confirm(fixture, 1).returncode, 0)
        self.commit_parent("reviewed")
        self.start_parent()

    # ---- 1. --feedback

    def test_feedback_makes_a_scaffold_that_passes_the_approval_check(self):
        self.reviewed()
        made = self.revise("--feedback")
        self.assertEqual(made.returncode, 0, made.stdout + made.stderr)
        with open(self.todo_path(), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("feedback: []", text)
        for name in ticket_model.SCRIPT_FIELDS:
            self.assertNotRegex(text, rf"(?m)^{name}: \S", name)
        self.assertIn("--agree --preview --verify i0001", made.stdout)
        self.assertEqual(sum(1 for x in made.stdout.splitlines() if x.startswith("次: ")), 1)
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "revise")
        verify = self.ccnavi("--agree", "--preview", "--verify", "i0001")
        self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)
        approved = self.ccnavi("user", "agree", stdin="y\n")
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        with open(os.path.join(self.approved, "doing", "i0001.md"), encoding="utf-8") as f:
            self.assertIn("feedback: []", f.read())

    # ---- 2. レビュー前は止まる

    def test_feedback_is_refused_before_the_last_review_is_done(self):
        self.requested()
        self.start_parent()
        refused = self.revise("--feedback")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("レビューが済んでから", refused.stderr)
        self.assertFalse(os.path.exists(self.todo_path()))

    def test_feedback_is_refused_when_the_parent_already_has_one(self):
        self.reviewed()
        self.assertEqual(self.revise("--feedback").returncode, 0)
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "revise")
        self.assertEqual(self.ccnavi("user", "agree", stdin="y\n").returncode, 0)
        if os.path.exists(self.todo_path()):
            os.remove(self.todo_path())
        refused = self.revise("--feedback")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("既にある", refused.stderr)

    # ---- 3. 写しだけ

    def test_without_the_flag_only_the_copy_is_made(self):
        self.reviewed()
        made = self.revise()
        self.assertEqual(made.returncode, 0, made.stdout + made.stderr)
        with open(self.todo_path(), encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("feedback:", text)
        self.assertIn("直してから", made.stdout)

    # ---- 4. 止まる場合

    def test_an_existing_proposal_is_not_overwritten(self):
        self.reviewed()
        self.assertEqual(self.revise().returncode, 0)
        with open(self.todo_path(), "a", encoding="utf-8") as f:
            f.write("\n書きかけ\n")
        again = self.revise()
        self.assertNotEqual(again.returncode, 0)
        self.assertIn("既にある", again.stderr)
        with open(self.todo_path(), encoding="utf-8") as f:
            self.assertIn("書きかけ", f.read())

    def test_no_approved_parent_a_child_id_and_a_wrong_cwd_stop(self):
        self.family()
        os.remove(os.path.join(self.approved, "doing", "i0001.md"))
        self.assertIn("承認済みの親が", self.revise().stderr)
        self.assertIn("子の識別子", self.revise(ident="i0001-01-01").stderr)
        self.assertIn("の中ではない", self.revise(cwd=self.root).stderr)
        self.assertFalse(os.path.exists(self.todo_path()))

    # ---- 5. finish の案内

    def test_finish_says_how_to_make_the_feedback_scaffold(self):
        self.reviewed()
        refused = self.ccnavi("ticket", "finish", "i0001")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("ticket revise i0001 --feedback", refused.stderr)
