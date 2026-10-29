"""フィードバック計画の承認は、レビューの状態を変えない。

1. 全体計画の最後のフェーズがレビュー済みでなければ、フィードバック計画を初めて足す改版は拒む
   （依頼しただけ・未解決の指摘が残ったままでも拒む）
2. レビュー済みなら通る。手で `done/` に動かした子の確定（`via: manual`）の後でも通る
3. 承認の前後で、計画の差し替え以外（`reviewed` のバイト列・`accepted.json`・子の置き場・
   `settled_by`）は変わらない
4. まとめて承認するとき、フィードバック計画の初回の改版と、同じ親の全体計画の番号の子が同じ承認に
   入っていたら、親の改版と該当する子を両方拒む。無関係の親子は通り、フィードバック計画のフェーズの
   子との同時承認は許す。絞り込みの有無で結果は変わらない
"""

from __future__ import annotations

import json
import os
import shutil

from ccnavi.tickets.approval import marks as approval_marks
from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import git, read_json, write
from tests.ticketsample import child_text, parent_text

FEEDBACK = ["implement-feedback"]


def _snapshot(root: str) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for base, _, names in os.walk(root):
        for name in names:
            path = os.path.join(base, name)
            with open(path, "rb") as f:
                out[os.path.relpath(path, root)] = f.read()
    return out


class FeedbackPlanApprovalTest(PhaseHarness):
    def requested(self):
        """フェーズ 1 の子を閉じて合流し、レビューを依頼したところまで。"""
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
        self.commit_parent("request")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        return fixture

    def propose_feedback(self, feedback=None):
        self.start_parent()
        plan = FEEDBACK if feedback is None else feedback
        self.propose("i0001", parent_text("i0001", ["design"], feedback=plan))

    def phase_dir(self):
        return os.path.join(self.approved, "phases", "i0001")

    # ---- 1. レビュー済みでなければ拒む

    def test_requested_only_is_refused(self):
        self.requested()
        self.propose_feedback()
        refused = self.approve()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("レビューが済んでから", refused.stderr)
        self.assertIsNone(approval_marks.read_mark(self.approved, "i0001", 1, "reviewed"))

    def test_unresolved_threads_keep_the_last_review_open_and_the_plan_refused(self):
        fixture = self.requested()
        data = read_json(fixture)
        data["threads"] = [{"id": "t0", "resolved": False, "url": "u/7#t0", "body": "直して"}]
        write(fixture, json.dumps(data))
        self.assertNotEqual(self.confirm(fixture, 1).returncode, 0)
        self.propose_feedback()
        refused = self.approve()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("レビューが済んでから", refused.stderr)
        # 拒んだだけで、何も動かしていない
        self.assertIsNone(approval_marks.read_mark(self.approved, "i0001", 1, "reviewed"))
        self.assertFalse(os.path.exists(os.path.join(self.phase_dir(), "accepted.json")))

    # ---- 2. レビュー済みなら通る

    def test_reviewed_passes_and_nothing_but_the_plan_changes(self):
        fixture = self.requested()
        self.assertEqual(self.confirm(fixture, 1).returncode, 0)
        self.commit_parent("reviewed")
        self.propose_feedback()
        before = _snapshot(self.approved)
        approved = self.ccnavi("user", "agree", stdin="y\n")
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        self.assertNotIn("済ませた扱い", approved.stdout)
        self.assertNotIn("レビュー待ちの子を閉じた", approved.stdout)
        after = _snapshot(self.approved)
        changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
        self.assertEqual(changed, {os.path.join("doing", "i0001.md")})
        self.assertIn(b"feedback:", after[os.path.join("doing", "i0001.md")])
        with open(os.path.join(self.approved, "doing", "i0001.md"), encoding="utf-8") as f:
            self.assertNotIn("feedback-plan", f.read())

    def test_a_child_in_review_stays_where_it_is(self):
        """承認は `review/` の子を `done/` へ動かさず、`settled_by` も書かない。"""
        self.requested()
        review_dir = os.path.join(self.parent_tree, "wip", "proposals", "review")
        self.assertTrue(os.path.exists(os.path.join(review_dir, "i0001-01-01.md")))
        self.propose_feedback()
        refused = self.approve()
        self.assertNotEqual(refused.returncode, 0)
        self.assertTrue(os.path.exists(os.path.join(review_dir, "i0001-01-01.md")))
        self.assertFalse(os.path.exists(os.path.join(self.approved, "done", "i0001-01-01.md")))
        with open(os.path.join(review_dir, "i0001-01-01.md"), encoding="utf-8") as f:
            self.assertNotIn("settled_by", f.read())

    def test_passes_after_a_manually_moved_child_is_settled(self):
        fixture = self.requested()
        waiting = os.path.join(self.parent_tree, "wip", "proposals", "review", "i0001-01-01.md")
        done = os.path.join(self.approved, "done", "i0001-01-01.md")
        os.makedirs(os.path.dirname(done), exist_ok=True)
        shutil.move(waiting, done)
        self.commit_parent("move by hand")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        result = write(
            os.path.join(self.root, "result.json"),
            json.dumps(
                {
                    "host": "fixture",
                    "mr": {"number": 7, "url": "u/7", "draft": True},
                    "threads": [],
                    "reviews": [],
                }
            ),
        )
        self.assertEqual(self.confirm(result, 1).returncode, 0)
        mark = approval_marks.read_mark(self.approved, "i0001", 1, "reviewed")
        self.assertEqual(mark["via"], "manual")
        self.commit_parent("settled")
        self.propose_feedback(feedback=[])
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        self.assertEqual(
            approval_marks.read_mark(self.approved, "i0001", 1, "reviewed")["via"], "manual"
        )
        del fixture

    # ---- 4. まとめて承認する

    def reviewed_with_plan_child(self):
        fixture = self.requested()
        self.assertEqual(self.confirm(fixture, 1).returncode, 0)
        self.commit_parent("reviewed")
        self.propose_feedback()
        self.propose("i0001-01-02", child_text("i0001-01-02", "i0001", 1, ["wip/design/*"]))
        return fixture

    def todo(self, name):
        return os.path.join(self.parent_tree, "wip", "proposals", "todo", name + ".md")

    def preview(self, *only):
        shown = self.ccnavi("--agree", "--preview", "--json", *only)
        return json.loads(shown.stdout)

    def test_the_revision_and_the_plan_child_are_both_refused(self):
        self.reviewed_with_plan_child()
        for only in ((), ("i0001", "i0001-01-02")):
            shown = self.preview(*only)
            self.assertEqual(shown["batch"], [], only)
            refused = {r["ticket"]: "\n".join(r["problems"]) for r in shown["rejected"]}
            self.assertEqual(sorted(refused), ["i0001", "i0001-01-02"], only)
            for text in refused.values():
                self.assertIn("全体計画に足す子を先に承認し", text)
                self.assertIn("再レビュー（レビュー済み）を済ませてから", text)
                self.assertIn("フィードバック計画の改版を承認する", text)
        # 承認は何も置かない
        refused = self.approve()
        self.assertTrue(os.path.exists(self.todo("i0001")))
        self.assertTrue(os.path.exists(self.todo("i0001-01-02")))
        self.assertFalse(os.path.exists(os.path.join(self.approved, "doing", "i0001-01-02.md")))
        with open(os.path.join(self.approved, "doing", "i0001.md"), encoding="utf-8") as f:
            self.assertNotIn("feedback:", f.read())
        del refused

    def test_the_plan_child_first_then_the_revision_after_the_review(self):
        fixture = self.reviewed_with_plan_child()
        os.rename(self.todo("i0001"), os.path.join(self.root, "revision.md"))
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.approved, "doing", "i0001-01-02.md")))
        # 子を足すとフェーズは開き直る。作業と再レビューを済ませるまで、改版は通らない
        shutil.move(os.path.join(self.root, "revision.md"), self.todo("i0001"))
        again = self.approve()
        self.assertNotEqual(again.returncode, 0)
        self.assertIn("レビューが済んでから", again.stderr)
        del fixture

    def test_a_feedback_phase_child_may_be_approved_with_the_revision(self):
        fixture = self.requested()
        self.assertEqual(self.confirm(fixture, 1).returncode, 0)
        self.commit_parent("reviewed")
        self.propose_feedback()
        self.propose("i0001-02-02", child_text("i0001-02-02", "i0001", 2, ["src/*"]))
        shown = self.preview()
        self.assertEqual(shown["rejected"], [], shown["rejected"])
        self.assertEqual(sorted(b["ticket"] for b in shown["batch"]), ["i0001", "i0001-02-02"])
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.approved, "doing", "i0001-02-02.md")))

    def test_an_unrelated_family_is_approved_by_the_partial_approval_rules(self):
        self.reviewed_with_plan_child()
        other = self.worktree("i0002", "main")
        self.propose("i0002", parent_text("i0002", ["design"]), tree=other)
        shown = self.preview()
        refused = sorted(r["ticket"] for r in shown["rejected"])
        self.assertEqual(refused, ["i0001", "i0001-01-02"])
        self.assertNotIn("i0002", refused)
