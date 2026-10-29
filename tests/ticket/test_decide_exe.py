"""decide の適用（実行ファイル側）の取りこぼしを確かめる受入テスト。

sh の代役ではなく、本物の `ccnavi user reviewed --accept-unresolved` と操作記録で見る。

1. AT43: ホストに返信・解決を書いたあとの取り直しで、選んだ指摘の本文が変わっていたら、続きの子も
   マーカーも置かず、解決した分を操作記録の applied に残して止まる（ボードは `mismatch` で見せ直す）
2. AT13: fix で作る続きの子に `ccnavi_op` が書かれる。同じ操作の打ち直しで 2 つ目の子を作らない
3. AT24: 引き継ぎ未完了の操作記録は、親の finish・ready・片付け（ops retire）で消えず、
   status が言い続ける

TicketTest・PhaseHarness の道具を借りる。借りた側のテストは回さない。
"""

from __future__ import annotations

import json
import os
import unittest

from ccnavi.tickets.model import oplog
from tests.ticket import test_phases, test_ticket
from tests.ticket.test_ticket import git, read_json, write
from tests.ticketsample import parent_text


def _borrow_tools_only(cls):
    """継いだテストを無効にする（unittest は呼べないものを集めない）。"""
    for name in dir(cls):
        if name.startswith("test") and name not in cls.__dict__:
            setattr(cls, name, None)
    return cls


@_borrow_tools_only
class DecideApplyTest(test_ticket.TicketTest):
    def planned(self, choices: dict):
        """受け入れの計画まで書く（`--accept-plan`）。

        返すのは (fixture, choices の JSON, ダイジェスト, 計画, 操作ID)。
        """
        fixture = self.two_threads()
        digest = json.loads(self.decide(fixture, "--preview", "--json").stdout)["digest"]
        text = json.dumps(choices)
        plan = os.path.join(self.state, "plan.json")
        made = self.decide(
            fixture, "--yes", text, "--digest", digest, "--json", "--accept-plan", plan
        )
        self.assertEqual(made.returncode, 0, made.stderr)
        return fixture, text, digest, plan, read_json(plan)["op"]

    def host_resolved(self, fixture, *ids: str, edit: dict | None = None) -> None:
        """sh が返信・解決したあとの、取り直した結果（ホストの実状態）を作る。"""
        data = read_json(fixture)
        for t in data["threads"]:
            if t["id"] in ids:
                t["resolved"] = True
            if edit and t["id"] in edit:
                t["body"] = edit[t["id"]]
        write(fixture, json.dumps(data))

    def op_record(self, op_id: str):
        return read_json(os.path.join(self.state, "ops", op_id + ".json"))

    # ---- AT43

    def test_a_changed_body_after_the_host_write_places_nothing_and_keeps_the_resolved_part(self):
        """AT43"""
        fixture, choices, digest, plan, op = self.planned({"u1": "keep", "u2": "fix"})
        # sh が u1 を解決した。2 回目の取得で、解決したスレッドの本文が変わっている
        self.host_resolved(fixture, "t1", edit={"t1": "命名（書き換えた）"})
        done = self.decide(
            fixture, "--yes", choices, "--digest", digest, "--json", "--accept-done", plan
        )
        self.assertNotEqual(done.returncode, 0)
        answer = json.loads(done.stdout)
        # ボードが読む答え（見せ直しを求める）
        self.assertTrue(answer["mismatch"])
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["changed"], ["u1"])
        self.assertIn("見せた指摘と今の指摘が違う。何も置いていない", done.stderr)
        phases = os.path.join(self.approved, "phases", "i0001")
        # 続きの子もマーカーも置かない。子は動かさない
        self.assertFalse(os.path.exists(os.path.join(self.approved, "doing", "i0001-01-03.md")))
        self.assertFalse(os.path.exists(os.path.join(phases, "1.reviewed")))
        self.assertTrue(os.path.exists(os.path.join(phases, "1.requested")))
        for child in ("i0001-01-01", "i0001-01-02"):
            self.assertFalse(os.path.exists(os.path.join(self.approved, "done", child + ".md")))
        # ホストで解決した分は、操作記録の applied に残る。適用（local）は立たない
        record = self.op_record(op)
        self.assertEqual(record["applied"]["accepted_on_host"], ["u1"])
        self.assertNotIn("local", record["applied"])
        # 設計との違い: 受け入れ台帳（accepted.json、accepted_by: decide）には書かない
        self.assertFalse(os.path.exists(os.path.join(phases, "accepted.json")))

    def test_a_changed_body_of_the_fix_thread_after_the_host_write_places_nothing(self):
        """AT43（直す指摘の本文が変わった場合。ホストで解決した分は keep の 1 件だけ）"""
        fixture, choices, digest, plan, op = self.planned({"u1": "keep", "u2": "fix"})
        self.host_resolved(fixture, "t1", edit={"t2": "テスト（書き換えた）"})
        done = self.decide(
            fixture, "--yes", choices, "--digest", digest, "--json", "--accept-done", plan
        )
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(json.loads(done.stdout)["changed"], ["u2"])
        self.assertFalse(os.path.exists(os.path.join(self.approved, "doing", "i0001-01-03.md")))
        self.assertEqual(self.op_record(op)["applied"]["accepted_on_host"], ["u1"])

    # ---- AT13

    def test_the_followup_child_carries_the_operation_id(self):
        """AT13"""
        fixture, choices, digest, plan, op = self.planned({"u1": "keep", "u2": "fix"})
        self.host_resolved(fixture, "t1")
        done = self.decide(
            fixture, "--yes", choices, "--digest", digest, "--json", "--accept-done", plan
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)["followup"], "i0001-01-03")
        with open(os.path.join(self.approved, "doing", "i0001-01-03.md"), encoding="utf-8") as f:
            self.assertIn(f"ccnavi_op: {op}", f.read())
        self.assertEqual(self.op_record(op)["applied"]["followup"], "i0001-01-03")

    def test_the_same_operation_replayed_after_the_marker_came_back_makes_no_second_child(self):
        """AT13（同じ操作の打ち直し。印が戻ったのに子が残った形）"""
        fixture, choices, digest, plan, op = self.planned({"u1": "keep", "u2": "fix"})
        self.host_resolved(fixture, "t1")
        marker = os.path.join(self.approved, "phases", "i0001", "1.requested")
        with open(marker, encoding="utf-8") as f:
            saved = f.read()
        args = ("--yes", choices, "--digest", digest, "--json", "--accept-done", plan)
        self.assertEqual(self.decide(fixture, *args).returncode, 0)
        write(marker, saved)
        again = self.decide(fixture, *args)
        doing = sorted(os.listdir(os.path.join(self.approved, "doing")))
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(
            [n for n in doing if n.startswith("i0001-01-")],
            ["i0001-01-03.md"],
            "同じ操作ID の子があるのに、2 つ目（i0001-01-04）を作った",
        )

    test_the_same_operation_replayed_after_the_marker_came_back_makes_no_second_child = (
        unittest.expectedFailure(
            # ccnavi_op は書くだけで、同じ操作IDの子を探して使い回さない
            test_the_same_operation_replayed_after_the_marker_came_back_makes_no_second_child
        )
    )

    def test_a_plain_replay_is_refused_because_the_request_mark_is_gone(self):
        """AT13（印が消えたままの打ち直しは、子を増やさず断る。設計の「使い回す」とは違い、断る）"""
        fixture, choices, digest, plan, op = self.planned({"u1": "keep", "u2": "fix"})
        self.host_resolved(fixture, "t1")
        args = ("--yes", choices, "--digest", digest, "--json", "--accept-done", plan)
        self.assertEqual(self.decide(fixture, *args).returncode, 0)
        again = self.decide(fixture, *args)
        self.assertNotEqual(again.returncode, 0)
        self.assertIn("依頼の記録がありません", again.stderr)
        doing = sorted(os.listdir(os.path.join(self.approved, "doing")))
        self.assertEqual([n for n in doing if n.startswith("i0001-01-")], ["i0001-01-03.md"])


@_borrow_tools_only
class UnfinishedOpsTest(test_phases.PhaseHarness):
    """AT24"""

    def ready(self, fixture):
        return self.ccnavi("--cwd", self.parent_tree, "review", "ready", "--result", fixture)

    def status(self):
        done = self.ccnavi("ticket", "status")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return done.stdout

    def leave_an_unfinished_decide(self) -> str:
        op = oplog.begin(
            self.state,
            oplog.KIND_DECIDE,
            repo={"host": "fixture", "mr_url": "u/7"},
            parent="i0001",
            phase=1,
            mr=7,
            stages=[("reply", oplog.STAGE_REPLY), ("note", oplog.STAGE_COMMENT)],
        )
        oplog.stage_set(self.state, op, "reply", oplog.SENT)
        oplog.stage_set(self.state, op, "note", oplog.UNKNOWN)
        return op

    def test_the_record_survives_the_finish_and_the_ready_and_the_cleanup(self):
        self.family(plan=["design"])
        self.propose(
            "i0001-01-01", test_phases.child_text("i0001-01-01", "i0001", 1, ["wip/design/*"])
        )
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        fixture = self.remote()
        self.run_child("i0001-01-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01-01")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        self.assertEqual(self.request(fixture, 1).returncode, 0)
        self.assertEqual(self.confirm(fixture, 1).returncode, 0)
        op = self.leave_an_unfinished_decide()
        self.assertIn("引き継ぎ未完了", self.status())
        # 親の finish は進む。status は言い続ける
        self.start_parent()
        self.propose("i0001", parent_text("i0001", ["design"], feedback=[]))
        self.assertEqual(self.approve().returncode, 0)
        closed = self.ccnavi("ticket", "finish", "i0001")
        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.assertIn("引き継ぎ未完了", self.status())
        self.commit_parent("状態の移動")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        # ready も進む。sh が ready のあとに打つ片付け（ops retire）も、未完了の記録は消さない
        passed = self.ready(fixture)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        retired = self.ccnavi("ops", "retire", "i0001")
        self.assertEqual(retired.returncode, 0, retired.stderr)
        self.assertIsNotNone(oplog.load(self.state, op))
        self.assertEqual([r["id"] for r in oplog.pending(self.state)], [op])
        self.assertIn("引き継ぎ未完了", self.status())
        self.assertIn(op[:8], self.status())

    def test_the_cleanup_removes_only_the_finished_records_of_the_parent(self):
        done = oplog.begin(
            self.state, oplog.KIND_COMMENT, repo={}, parent="i0001", stages=[("c", "comment")]
        )
        oplog.stage_set(self.state, done, "c", oplog.SENT)
        left = self.leave_an_unfinished_decide()
        self.assertEqual(self.ccnavi("ops", "retire", "i0001").returncode, 0)
        self.assertIsNone(oplog.load(self.state, done))
        self.assertIsNotNone(oplog.load(self.state, left))


if __name__ == "__main__":
    unittest.main()
