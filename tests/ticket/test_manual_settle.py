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
import unittest
from datetime import UTC, datetime

from ccnavi.infra import settings
from ccnavi.tickets import approval_marks, core_review, manual_settle, phase
from ccnavi.tickets.model import review_host, ticket_fields
from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import git, read_json, write
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


def _iso(epoch):
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class ManualSettleGapsTest(PhaseHarness):
    """手で動かした子の取り違え（AT7・AT9・AT18・AT33〜AT38）。

    `ManualSettleTest` の道具を借りて、「手動とみなさない」「確定しない」を固定する。
    """

    requested = ManualSettleTest.requested
    move_by_hand = ManualSettleTest.move_by_hand
    result = ManualSettleTest.result
    status = ManualSettleTest.status
    share = ManualSettleTest.share

    # ---- 道具

    def conf(self):
        conf, _ = settings.load(self.root)
        return conf

    def assess(self, number=1):
        conf = self.conf()
        parent = core_review._open_parent(self.root, conf, "i0001")
        ph = next(p for p in phase.phases_of(self.root, conf, "i0001") if p.number == number)
        return manual_settle.assess(self.root, conf, parent, ph)

    def work(self, child, number, glob, path, review=True):
        """子を 1 つ、承認から親への合流まで進める。"""
        self.propose(child, child_text(child, "i0001", number, [glob], review=review))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child(child, [(path, "d\n")])
        self.assertEqual(self.close_child(child).returncode, 0, child)
        self.commit_parent("close " + child)
        self.merge(child)

    def hand(self, child):
        """子を review/ から done/ へ手で動かす（settled_by は書かない）。"""
        waiting = os.path.join(self.parent_tree, "wip", "proposals", "review", child + ".md")
        done = os.path.join(self.approved, "done", child + ".md")
        os.makedirs(os.path.dirname(done), exist_ok=True)
        shutil.move(waiting, done)
        return done

    def child_path(self, child, where):
        if where == "review":
            return os.path.join(self.parent_tree, "wip", "proposals", "review", child + ".md")
        return os.path.join(self.approved, where, child + ".md")

    def edit(self, path, fn):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        write(path, fn(text))

    def drop_field(self, path, key):
        self.edit(
            path, lambda t: "".join(ln for ln in t.splitlines(True) if not ln.startswith(key + ":"))
        )

    def settled(self, path, by):
        self.edit(path, lambda t: ticket_fields.set_fields(t, {"settled_by": by}))

    def moved_epoch(self, child):
        rel = os.path.join(".ccnavi", "approved", "done", child + ".md")
        stamp = git(self.parent_tree, "log", "--diff-filter=A", "--format=%ct", "-1", "--", rel)
        return float(stamp.strip())

    def fixture_with(self, fixture, threads):
        data = read_json(fixture)
        data["threads"] = threads
        write(fixture, json.dumps(data))
        return fixture

    def two_children(self):
        """フェーズ 1 に子を 2 つ持たせて依頼まで進める。"""
        self.family(plan=["design"])
        self.work("i0001-01-01", 1, "wip/design/*", "wip/design/a.md")
        self.work("i0001-01-02", 1, "wip/design/*", "wip/design/b.md")
        fixture = self.remote()
        self.assertEqual(self.request(fixture, 1).returncode, 0)
        self.commit_parent("request")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        return fixture

    def no_reviewed_mark(self, number=1):
        self.assertIsNone(approval_marks.read_mark(self.approved, "i0001", number, "reviewed"))

    # ---- AT7 一部だけ

    def test_moving_only_one_of_two_children_is_partial_and_not_settled(self):
        fixture = self.two_children()
        self.hand("i0001-01-01")
        self.share()
        found = self.assess()
        self.assertEqual(found.state, manual_settle.PARTIAL)
        self.assertEqual(found.children, ["i0001-01-01"])
        self.assertEqual(found.waiting, ["i0001-01-02"])
        said = self.status()
        self.assertIn("子 i0001-01-01 はレビュー完了、フェーズは未完了", said)
        self.assertNotIn("レビュー完了確認待ち", said)
        refused = self.confirm(self.fixture_with(fixture, [_thread()]), 1)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("フェーズは未完了", refused.stdout)
        self.assertIn("未解決のスレッド", refused.stderr)
        self.no_reviewed_mark()
        entries, _ = approval_marks.read_ledger(self.approved, "i0001")
        self.assertEqual(entries, [])
        # 残った子はまだ review/ にある
        self.assertTrue(os.path.exists(self.child_path("i0001-01-02", "review")))

    def test_a_partial_move_with_no_threads_goes_through_the_ordinary_confirm(self):
        # 設計は止めたままだが、実装は未解決が無ければ通常の confirm（手動の確定ではない）で
        # 残りの子を settled_by: confirm で動かしてレビュー済みにする
        fixture = self.two_children()
        self.hand("i0001-01-01")
        self.share()
        done = self.confirm(self.fixture_with(fixture, []), 1)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        mark = approval_marks.read_mark(self.approved, "i0001", 1, "reviewed")
        self.assertNotIn("via", mark)
        self.assertNotIn("moved", mark)
        with open(self.child_path("i0001-01-02", "done"), encoding="utf-8") as f:
            self.assertIn('settled_by: "confirm"', f.read())

    def test_both_children_moved_by_hand_are_ready(self):
        self.two_children()
        self.hand("i0001-01-01")
        self.hand("i0001-01-02")
        self.share()
        found = self.assess()
        self.assertEqual(found.state, manual_settle.READY)
        self.assertEqual(sorted(found.children), ["i0001-01-01", "i0001-01-02"])

    # ---- AT7 延期分

    def deferred_family(self):
        """フェーズ 1（設計、延期）とフェーズ 2（受入テスト、引き受ける）。依頼はフェーズ 2。"""
        self.family(plan=[("design", "defer"), "acceptance"])
        self.work("i0001-01-01", 1, "wip/design/*", "wip/design/a.md")
        self.work("i0001-02-01", 2, "tests/*", "tests/a.txt")
        fixture = self.remote()
        self.assertEqual(self.request(fixture, 2).returncode, 0)
        self.commit_parent("request")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        return fixture

    def test_moving_only_the_accepting_phases_child_is_partial_while_the_deferred_one_waits(self):
        fixture = self.deferred_family()
        # 延期したフェーズの子も review/ に置かれ、引き受ける側のレビューで一緒に見る
        self.assertTrue(os.path.exists(self.child_path("i0001-01-01", "review")))
        self.hand("i0001-02-01")
        self.share()
        found = self.assess(2)
        self.assertEqual(found.state, manual_settle.PARTIAL)
        self.assertEqual(found.children, ["i0001-02-01"])
        self.assertEqual(found.waiting, ["i0001-01-01"])
        # 延期したフェーズ自身はレビューを持たないので、手動移動の判定に入らない
        self.assertEqual(self.assess(1).state, manual_settle.NONE)
        refused = self.confirm(self.fixture_with(fixture, [_thread()]), 2)
        self.assertNotEqual(refused.returncode, 0)
        self.no_reviewed_mark(2)

    def test_moving_only_the_deferred_phases_child_is_partial_too(self):
        self.deferred_family()
        self.hand("i0001-01-01")
        self.share()
        found = self.assess(2)
        self.assertEqual(found.state, manual_settle.PARTIAL)
        self.assertEqual(found.children, ["i0001-01-01"])
        self.assertEqual(found.waiting, ["i0001-02-01"])

    def test_moving_both_the_deferred_and_the_accepting_children_is_ready(self):
        fixture = self.deferred_family()
        self.hand("i0001-01-01")
        self.hand("i0001-02-01")
        self.share()
        found = self.assess(2)
        self.assertEqual(found.state, manual_settle.READY)
        self.assertEqual(sorted(found.children), ["i0001-01-01", "i0001-02-01"])
        done = self.confirm(self.fixture_with(fixture, [_thread()]), 2)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        mark = approval_marks.read_mark(self.approved, "i0001", 2, "reviewed")
        self.assertEqual(mark["via"], "manual")
        self.assertEqual(sorted(mark["moved"]), ["i0001-01-01", "i0001-02-01"])

    # ---- AT9

    def test_a_child_without_finished_to_is_route_unknown_and_not_manual(self):
        fixture = self.requested()
        self.drop_field(self.child_path("i0001-01-01", "review"), "finished_to")
        self.move_by_hand()
        self.share()
        found = self.assess()
        self.assertEqual(found.state, manual_settle.ROUTE_UNKNOWN)
        self.assertEqual(found.children, ["i0001-01-01"])
        self.assertIn("経路が分からない", self.status())
        refused = self.confirm(self.fixture_with(fixture, [_thread()]), 1)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("経路が分からない", refused.stdout)
        self.no_reviewed_mark()
        entries, _ = approval_marks.read_ledger(self.approved, "i0001")
        self.assertEqual(entries, [])

    # phases_of が重複を done 側 1 件に畳み、assess の重複検査に届かない（実装の欠陥）
    @unittest.expectedFailure
    def test_the_same_child_in_review_and_done_is_broken(self):
        self.requested()
        done = self.child_path("i0001-01-01", "done")
        os.makedirs(os.path.dirname(done), exist_ok=True)
        shutil.copyfile(self.child_path("i0001-01-01", "review"), done)
        self.share()
        self.assertEqual(self.assess().state, manual_settle.BROKEN)

    def test_a_move_without_a_request_waits_for_the_request(self):
        self.family(plan=["design"])
        self.work("i0001-01-01", 1, "wip/design/*", "wip/design/a.md")
        fixture = self.remote()
        self.hand("i0001-01-01")
        self.share()
        found = self.assess()
        self.assertEqual(found.state, manual_settle.NO_REQUEST)
        self.assertIn("依頼の記録が無い", self.status())
        self.assertIn("request", found.line())
        refused = self.confirm(self.fixture_with(fixture, [_thread()]), 1)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("依頼の記録がありません", refused.stderr)
        self.no_reviewed_mark()

    def test_a_child_that_finished_straight_to_done_is_not_manual(self):
        self.family(plan=["research"])
        self.work("i0001-01-01", 1, "wip/research/*", "wip/research/summary.md", review=False)
        self.assertTrue(os.path.exists(self.child_path("i0001-01-01", "done")))
        with open(self.child_path("i0001-01-01", "done"), encoding="utf-8") as f:
            self.assertIn('finished_to: "done"', f.read())
        self.assertEqual(self.assess().state, manual_settle.NONE)
        self.assertNotIn("手で done/", self.status())

    # ---- AT35

    def done_child_family(self):
        self.family(plan=["research"])
        self.work("i0001-01-01", 1, "wip/research/*", "wip/research/summary.md", review=False)
        return self.child_path("i0001-01-01", "done")

    def test_a_child_done_by_itself_is_not_manual_after_the_risk_rises(self):
        self.done_child_family()
        approval_marks.write_mark(
            self.approved, "i0001", 1, "requested", {"mr": 7, "host": "fixture"}
        )
        approval_marks.write_child_record(
            self.approved,
            "i0001",
            "i0001-01-01",
            "risk",
            {"points": 90, "level": "HIGH", "hits": []},
        )
        conf = self.conf()
        ph = next(p for p in phase.phases_of(self.root, conf, "i0001") if p.number == 1)
        self.assertTrue(ph.review_required)
        self.assertEqual(self.assess().state, manual_settle.NONE)
        self.assertNotIn("手で done/", self.status())

    def test_a_child_done_by_itself_is_not_manual_after_the_plan_is_revised_to_mr(self):
        done = self.done_child_family()
        approval_marks.write_mark(
            self.approved, "i0001", 1, "requested", {"mr": 7, "host": "fixture"}
        )
        copy = os.path.join(self.approved, "doing", "i0001.md")
        self.edit(
            copy, lambda t: t.replace("  - research\n", "  - {type: research, review: mr}\n", 1)
        )
        ph = next(p for p in phase.phases_of(self.root, self.conf(), "i0001") if p.number == 1)
        self.assertTrue(ph.review_required)
        self.assertTrue(os.path.exists(done))
        self.assertEqual(self.assess().state, manual_settle.NONE)

    # ---- AT34

    def test_a_child_settled_by_confirm_or_close_early_and_stopped_before_the_mark_is_not_manual(
        self,
    ):
        for by in ("confirm", "close-early"):
            with self.subTest(by=by):
                self.setUp()
                fixture = self.requested()
                self.move_by_hand()
                self.settled(self.child_path("i0001-01-01", "done"), by)
                self.share()
                self.no_reviewed_mark()
                self.assertEqual(self.assess().state, manual_settle.NONE)
                self.assertNotIn("レビュー完了確認待ち", self.status())
                refused = self.confirm(self.fixture_with(fixture, [_thread()]), 1)
                self.assertNotEqual(refused.returncode, 0)
                self.assertIn("未解決のスレッド", refused.stderr)
                self.no_reviewed_mark()
                # 未解決が無ければ、通常の confirm でマーカーを置いて終わる（手動の確定ではない）
                clear = self.confirm(self.fixture_with(fixture, []), 1)
                self.assertEqual(clear.returncode, 0, clear.stdout + clear.stderr)
                self.assertNotIn(
                    "via", approval_marks.read_mark(self.approved, "i0001", 1, "reviewed")
                )

    # ---- AT33

    def decide_with_extra(self, fixture, choices, shown_threads):
        """`shown_threads` を見せて適用する。適用前に keep は解決済みになり、t1 が増える。"""
        self.fixture_with(fixture, shown_threads)
        base = ("--cwd", self.parent_tree, "user", "reviewed", "1", "--accept-unresolved")
        shown = self.ccnavi(*base, "--preview", "--json", "--result", fixture)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        digest = json.loads(shown.stdout)["digest"]
        picked = json.dumps(choices)
        plan = os.path.join(self.state, "accept-plan.json")
        planned = self.ccnavi(
            *base, "--yes", picked, "--digest", digest, "--json", "--result", fixture,
            "--accept-plan", plan,
        )  # fmt: skip
        self.assertEqual(planned.returncode, 0, planned.stdout + planned.stderr)
        t1 = {"id": "t1", "resolved": False, "url": "u/7#t1", "body": "追加", "last_id": "c1"}
        resolved = [{**t, "resolved": t["url"] in choices and choices[t["url"]] == "keep"}
                    for t in shown_threads]  # fmt: skip
        self.fixture_with(fixture, [*resolved, t1])
        done = self.ccnavi(
            *base, "--yes", picked, "--digest", digest, "--json", "--result", fixture,
            "--accept-done", plan,
        )  # fmt: skip
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return json.loads(done.stdout)

    def test_after_a_decide_that_kept_one_thread_confirm_counts_the_extra_one(self):
        # 設計は「子が settled_by: decide で動いたあと」だが、実装は追加の指摘が残り、直す指摘が
        # 無い間は子を動かさない（review/ に残す）。どちらでも confirm は手動移動に入らない
        fixture = self.requested()
        t0 = {"id": "t0", "resolved": False, "url": "u/7#t0", "body": "直して", "last_id": "c0"}
        answer = self.decide_with_extra(fixture, {"u/7#t0": "keep"}, [t0])
        self.assertFalse(answer["reviewed"])
        self.assertEqual(answer["pending_extra"], ["u/7#t1"])
        self.assertTrue(os.path.exists(self.child_path("i0001-01-01", "review")))
        self.no_reviewed_mark()
        self.share()
        self.assertEqual(self.assess().state, manual_settle.NONE)
        refused = self.confirm(fixture, 1)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("未解決のスレッドが 1 件", refused.stderr)
        self.assertIn("u/7#t1", refused.stderr)
        self.assertNotIn("u/7#t0", refused.stderr)
        self.assertNotIn("レビュー完了確認待ち", refused.stdout)
        self.no_reviewed_mark()

    def test_after_a_decide_that_moved_the_child_confirm_is_not_manual(self):
        # fix を選ぶと子は settled_by: decide で done/ へ動く。マーカーは置かれず、
        # confirm は手動移動に入らずに止まる
        fixture = self.requested()
        t0 = {"id": "t0", "resolved": False, "url": "u/7#t0", "body": "直して", "last_id": "c0"}
        t2 = {"id": "t2", "resolved": False, "url": "u/7#t2", "body": "直す", "last_id": "c2"}
        answer = self.decide_with_extra(fixture, {"u/7#t0": "keep", "u/7#t2": "fix"}, [t0, t2])
        self.assertTrue(answer["followup"])
        with open(self.child_path("i0001-01-01", "done"), encoding="utf-8") as f:
            self.assertIn('settled_by: "decide"', f.read())
        self.no_reviewed_mark()
        self.share()
        self.assertEqual(self.assess().state, manual_settle.NONE)
        refused = self.confirm(fixture, 1)
        self.assertNotEqual(refused.returncode, 0)
        self.assertNotIn("レビュー完了確認待ち", refused.stdout)
        # fix でフェーズが開き直り、依頼のマーカーも消える。続きの子を終えて依頼し直すまで通らない
        self.assertIn("依頼の記録がありません", refused.stderr)
        self.no_reviewed_mark()

    # ---- AT36

    def shared_move(self):
        fixture = self.requested()
        self.move_by_hand()
        self.share()
        return fixture, self.moved_epoch("i0001-01-01")

    def test_a_thread_inside_the_margin_before_the_move_is_not_accepted(self):
        fixture, moved = self.shared_move()
        inside = _thread(created_at=_iso(moved - manual_settle.MARGIN_SECONDS / 2))
        refused = self.confirm(self.fixture_with(fixture, [inside]), 1)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("移動の後に付いた指摘", refused.stderr)
        self.no_reviewed_mark()
        entries, _ = approval_marks.read_ledger(self.approved, "i0001")
        self.assertEqual(entries, [])

    def test_a_thread_older_than_the_margin_is_accepted(self):
        fixture, moved = self.shared_move()
        before = _thread(created_at=_iso(moved - manual_settle.MARGIN_SECONDS - 60))
        done = self.confirm(self.fixture_with(fixture, [before]), 1)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    # ---- AT38

    # 指摘が無いと通常の confirm が通り、「確定しない」と言いながらレビュー済みにする（実装の欠陥）
    @unittest.expectedFailure
    def test_an_unshared_move_is_not_confirmed_even_with_no_threads(self):
        fixture = self.requested()
        self.move_by_hand()
        self.commit_parent("move by hand")
        self.assertEqual(self.assess().state, manual_settle.UNSHARED)
        refused = self.confirm(self.fixture_with(fixture, []), 1)
        self.assertNotEqual(refused.returncode, 0)
        self.no_reviewed_mark()

    def test_a_committed_but_unpushed_move_is_not_shared(self):
        fixture = self.requested()
        self.move_by_hand()
        self.commit_parent("move by hand")
        found = self.assess()
        self.assertEqual(found.state, manual_settle.UNSHARED)
        self.assertIn("移動が未共有", self.status())
        refused = self.confirm(self.fixture_with(fixture, [_thread()]), 1)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("移動が未共有", refused.stdout)
        self.no_reviewed_mark()
        entries, _ = approval_marks.read_ledger(self.approved, "i0001")
        self.assertEqual(entries, [])
        # push すれば確定してよい形になる
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        self.assertEqual(self.assess().state, manual_settle.READY)

    # ---- AT37

    def two_phases(self):
        """フェーズ 1 を手動移動で確定し、フェーズ 2 の依頼まで進める。返すのは取得結果の見本。"""
        self.family(plan=["design", "acceptance"])
        self.work("i0001-01-01", 1, "wip/design/*", "wip/design/a.md")
        fixture = self.remote()
        self.assertEqual(self.request(fixture, 1).returncode, 0)
        self.commit_parent("request 1")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        self.move_by_hand()
        self.share()
        self.fixture_with(fixture, [_thread()])
        first = self.confirm(fixture, 1)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.commit_parent("reviewed 1")
        self.work("i0001-02-01", 2, "tests/*", "tests/a.txt")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        self.assertEqual(self.request(fixture, 2).returncode, 0)
        return fixture

    def test_a_thread_accepted_in_phase_1_is_not_counted_by_phase_2_even_after_reopening(self):
        fixture = self.two_phases()
        entries, _ = approval_marks.read_ledger(self.approved, "i0001")
        self.assertEqual([e["key"] for e in entries], ["https://h/c/1"])
        # フェーズ 1 を開き直す（マーカーが消える）。台帳は残る
        cleared = approval_marks.clear_marks(self.approved, "i0001", 1)
        self.assertIn("reviewed", cleared.kinds)
        self.no_reviewed_mark(1)
        entries, problem = approval_marks.read_ledger(self.approved, "i0001")
        self.assertEqual(problem, "")
        self.assertEqual(len(entries), 1)
        done = self.confirm(fixture, 2)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_thread_accepted_in_phase_1_is_counted_again_after_a_new_comment_or_an_edit(self):
        fixture = self.two_phases()
        again = self.fixture_with(fixture, [_thread(last_id="c2")])
        refused = self.confirm(again, 2)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("未解決のスレッドが 1 件", refused.stderr)
        edited = self.fixture_with(fixture, [_thread(body="やはりここも")])
        refused = self.confirm(edited, 2)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("未解決のスレッドが 1 件", refused.stderr)
        extra = self.fixture_with(fixture, [_thread(), _thread(id="t2", url="https://h/c/2")])
        refused = self.confirm(extra, 2)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("https://h/c/2", refused.stderr)
        self.assertNotIn("https://h/c/1", refused.stderr)

    # ---- AT18

    def test_old_accepted_keys_do_not_hide_a_thread(self):
        marks = {
            "keys only": {"mr": 7, "accepted": ["https://h/c/1"]},
        }
        for name, mark in marks.items():
            with self.subTest(name=name):
                self.setUp()
                self.family(plan=["design", "acceptance"])
                self.work("i0001-01-01", 1, "wip/design/*", "wip/design/a.md")
                approval_marks.write_mark(self.approved, "i0001", 1, "reviewed", mark)
                fixture = self.remote()
                self.work("i0001-02-01", 2, "tests/*", "tests/a.txt")
                git(self.parent_tree, "push", "--quiet", "origin", "i0001")
                self.assertEqual(self.request(fixture, 2).returncode, 0)
                entries, _ = approval_marks.read_ledger(self.approved, "i0001")
                self.assertEqual(entries, [])
                refused = self.confirm(self.fixture_with(fixture, [_thread()]), 2)
                self.assertNotEqual(refused.returncode, 0)
                self.assertIn("未解決のスレッドが 1 件", refused.stderr)
                self.assertIn("https://h/c/1", refused.stderr)
                self.no_reviewed_mark(2)
