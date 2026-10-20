"""手で動かした子の判定（`manual_settle`）と、confirm による版つきの確定、受け入れ台帳。

ユーザが子を `review/` から `done/` へ手で動かす（`settled_by` を持たない）と、親が confirm を
打ったときだけ、未解決の指摘を解決せずに台帳へ受け入れてレビュー済みにする。

1. 動かしただけ（未コミット）は確定しない。status が「移動が未共有」と言う
2. コミットして push すると「レビュー完了確認待ち」になり、confirm が通る。台帳に版つきで残り、
   reviewed マーカーは `via: manual` と `moved` を持つ
3. 移動の後に付いた指摘が残っていれば確定しない
4. 変更要求があれば保留する
5. 台帳に受け入れた指摘は、版が同じ間は数えず、本文が変わるか、受け入れた後にコメントが付けば数える
"""

from __future__ import annotations

import json
import os
import shutil

from ccnavi.tickets import approval_marks, review_host
from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import git, write
from tests.ticketsample import child_text

OLD = "2026-01-01T00:00:00Z"
FUTURE = "2999-01-01T00:00:00Z"


def _thread(**extra):
    return {
        "id": "t1",
        "resolved": False,
        "url": "https://h/c/1",
        "body": "ここを直して",
        "created_at": OLD,
        "last_id": "c1",
        **extra,
    }


class ManualSettleTest(PhaseHarness):
    def requested(self):
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

    def move_by_hand(self):
        waiting = os.path.join(self.parent_tree, "wip", "proposals", "review", "i0001-01-01.md")
        done = os.path.join(self.approved, "done", "i0001-01-01.md")
        os.makedirs(os.path.dirname(done), exist_ok=True)
        shutil.move(waiting, done)
        with open(done, encoding="utf-8") as f:
            self.assertNotIn("settled_by", f.read())

    def result(self, threads, reviews=()):
        return write(
            os.path.join(self.root, "result.json"),
            json.dumps(
                {
                    "host": "fixture",
                    "mr": {"number": 7, "url": "u/7", "draft": True},
                    "threads": threads,
                    "reviews": list(reviews),
                }
            ),
        )

    def status(self):
        result = self.ccnavi("ticket", "status", "i0001")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def share(self):
        self.commit_parent("move by hand")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")

    def test_an_uncommitted_move_is_not_shared_and_is_not_settled(self):
        self.requested()
        self.move_by_hand()
        self.assertIn("移動が未共有", self.status())
        refused = self.confirm(self.result([_thread()]), 1)
        self.assertNotEqual(refused.returncode, 0)

    def test_a_shared_move_is_settled_by_confirm_with_a_versioned_ledger(self):
        self.requested()
        self.move_by_hand()
        self.share()
        self.assertIn("レビュー完了確認待ち", self.status())
        done = self.confirm(self.result([_thread()]), 1)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        mark = approval_marks.read_mark(self.approved, "i0001", 1, "reviewed")
        self.assertEqual(mark["via"], "manual")
        self.assertEqual(mark["moved"], ["i0001-01-01"])
        entries, problem = approval_marks.read_ledger(self.approved, "i0001")
        self.assertEqual(problem, "")
        self.assertEqual(entries[0]["key"], "https://h/c/1")
        self.assertEqual(entries[0]["accepted_by"], "manual")
        self.assertEqual(entries[0]["last_comment_id"], "c1")
        self.assertTrue(entries[0]["version"])

    def test_a_thread_made_after_the_move_is_not_accepted(self):
        self.requested()
        self.move_by_hand()
        self.share()
        refused = self.confirm(self.result([_thread(created_at=FUTURE)]), 1)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("移動の後に付いた指摘", refused.stderr)
        self.assertIsNone(approval_marks.read_mark(self.approved, "i0001", 1, "reviewed"))

    def test_a_reply_added_after_the_move_is_not_accepted(self):
        self.requested()
        self.move_by_hand()
        self.share()
        refused = self.confirm(self.result([_thread(last_at=FUTURE, last_id="c9")]), 1)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("移動の後に付いた指摘", refused.stderr)

    def test_a_thread_with_an_unreadable_time_is_not_accepted(self):
        self.requested()
        self.move_by_hand()
        self.share()
        refused = self.confirm(self.result([_thread(created_at="")]), 1)
        self.assertNotEqual(refused.returncode, 0)

    def test_a_changes_requested_review_holds_the_move(self):
        self.requested()
        self.move_by_hand()
        self.share()
        review = {"state": "CHANGES_REQUESTED", "url": "u", "submitted_at": OLD, "author": "r"}
        refused = self.confirm(self.result([], [review]), 1)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("変更要求", refused.stderr)

    def test_stop_nudges_to_confirm_once_when_the_move_is_shared(self):
        self.requested()
        self.move_by_hand()
        self.share()
        first = self.hook("Stop", "", self.parent_tree)
        self.assertIn("NUDGE_TICKET_REQUEST", first.stdout)
        self.assertIn("confirm --phase 1", first.stdout)
        second = self.hook("Stop", "", self.parent_tree)
        self.assertNotIn("NUDGE_TICKET_REQUEST", second.stdout)

    def test_the_next_prompt_says_to_confirm_once_and_stop_does_not_repeat_it(self):
        self.requested()
        self.move_by_hand()
        self.share()
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "cwd": self.parent_tree,
            "session_id": "s1",
            "prompt": "続けて",
        }
        first = self.ccnavi("--mode", "enable", stdin=json.dumps(payload))
        self.assertIn("confirm --phase 1", first.stdout)
        second = self.ccnavi("--mode", "enable", stdin=json.dumps(payload))
        self.assertNotIn("confirm --phase 1", second.stdout)
        stop = self.hook("Stop", "", self.parent_tree)
        self.assertNotIn("confirm --phase 1", stop.stdout)


class LedgerCountingTest(PhaseHarness):
    def thread(self, **extra):
        return review_host.Thread(
            id="t1", url="https://h/c/1", body="ここを直して", last_comment_id="c1", **extra
        )

    def entry(self, t):
        return {
            "key": review_host.thread_key(t),
            "version": review_host.thread_version(t),
            "last_comment_id": t.last_comment_id,
        }

    def test_an_accepted_thread_is_not_counted_until_it_changes(self):
        t = self.thread()
        ledger = [self.entry(t)]
        self.assertEqual(review_host._unresolved([t], ledger=ledger), [])
        # 本文が変わると版が変わる
        edited = self.thread()
        edited.body = "やはりここを直して"
        self.assertEqual(review_host._unresolved([edited], ledger=ledger), [edited])
        # 受け入れた後にコメントが付く（再提起）
        again = self.thread()
        again.last_comment_id = "c2"
        self.assertEqual(review_host._unresolved([again], ledger=ledger), [again])
        # 台帳が無ければ数える
        self.assertEqual(review_host._unresolved([t]), [t])

    def test_a_thread_without_a_last_comment_id_is_never_hidden(self):
        t = self.thread()
        t.last_comment_id = ""
        ledger = [{**self.entry(t), "last_comment_id": ""}]
        self.assertEqual(review_host._unresolved([t], ledger=ledger), [t])

    def test_a_broken_ledger_hides_nothing(self):
        write(os.path.join(self.approved, "phases", "i0001", "accepted.json"), "{not json")
        entries, problem = approval_marks.read_ledger(self.approved, "i0001")
        self.assertEqual(entries, [])
        self.assertIn("読めない", problem)
        self.assertIn("読めない", approval_marks.add_ledger(self.approved, "i0001", [{"key": "k"}]))
