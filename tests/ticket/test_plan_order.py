"""計画の順序を振り直して書き戻す `ccnavi --plan-order <親>`（設計 9.7）の受入テスト。

VS Code のワークフロー編集タブが打つ経路。タブは図で引いた線（元の番号での直接の先行）を
`--order` に付けて実行ファイルに渡し、実行ファイルが番号を振り直して答える。保存では
`--write --expect <ハッシュ>` で提案の計画を書き換える。見るのは 8 つ。

1. 番号の振り直しの決まり。見本の表（tests/fixtures/plan-reorder.json）は拡張と共有する
2. `--plan-order <親> --json` は読むだけ。`--order` を付ければ振り直した答えを返す
3. `--order` の形の誤りは使い方の誤り。循環・子が承認された番号へ入る線・詰められない線は断る
4. `--write` は親の提案の `plan:` / `feedback:` の値だけを書き換え、ほかのバイトを変えない。
   `--expect` が今の中身と違う・順序の検査に error がある・断った線なら何も書かない
5. 改版では子が承認された番号を動かさず、改版の錠に落ちる形は書かない
6. 書き換えたあとの一覧で承認が通り、書き換えの前のダイジェストでは止まる。`--agree --preview
   --json` の `plans` は `--plan-order <親> --json` と同じ関数で作る
7. エージェントが打っても組み込みの止めに当たらない
8. 承認を渡す文は、計画を持つ親とその子に「番号は承認済みチケットで読み直す」と添える
"""

from __future__ import annotations

import json
import os
import unittest

import yaml

from ccnavi.tickets import agree_candidates, agree_screen, phase_forms, plan_order, ticket_model
from ccnavi.tickets import ticket as ticket_mod
from tests.ticket.test_phases import PhaseHarness, child_text, parent_text
from tests.ticket.test_ticket import ROOT

PLAN_REORDER = os.path.join(ROOT, "tests", "fixtures", "plan-reorder.json")

# 3 項の一直線。research → design → implement。
PLAN3 = ["research", {"type": "design", "after": [1]}, {"type": "implement", "after": [2]}]
# research が design を待つように線を逆に引いた形（元の番号での直接の先行）。
REVERSE = {"plan": {"1": [2], "2": [], "3": [1]}}


def parsed(plan, feedback=None):
    """計画の項（見本の表の形）を持つ親を読む。"""
    front = {"version": 1, "ticket": "i0001", "plan": plan}
    if feedback is not None:
        front["feedback"] = feedback
    front["title"] = "親"
    front["allow"] = [{"match": "Write|Edit", "glob": "src/*"}]
    text = "---\n" + yaml.safe_dump(front, allow_unicode=True, sort_keys=False) + "---\n本文\n"
    t, problems = ticket_mod.parse(text)
    assert t is not None, problems
    return t


def front_of(text: str) -> dict:
    return yaml.safe_load(text.split("---\n", 2)[1])


class ReorderTableTest(unittest.TestCase):
    """1. 番号の振り直しの決まりを、見本の表で固定する。"""

    def test_the_table(self):
        with open(PLAN_REORDER, encoding="utf-8") as f:
            cases = json.load(f)["cases"]
        self.assertTrue(cases)
        for case in cases:
            with self.subTest(case=case["name"]):
                t = parsed(case["plan"], case.get("feedback"))
                order, why = plan_order.parse_order(json.dumps(case["order"]), t)
                self.assertEqual(why, "")
                got = plan_order.renumber(t, order, set(case.get("fixed", [])))
                if "refused" in case:
                    self.assertEqual([r.kind for r in got.refused], [case["refused"]])
                    continue
                self.assertEqual(got.refused, [])
                expect = case["expect"]
                self.assertEqual([i.as_raw() for i in got.plan], expect["plan"])
                if "feedback" in expect:
                    self.assertEqual([i.as_raw() for i in got.feedback], expect["feedback"])
                back = {new: old for old, new in got.moved.items()}
                self.assertEqual([back[n] for n in range(1, len(back) + 1)], expect["from"])

    def test_without_order_nothing_moves(self):
        t = parsed(PLAN3)
        got = plan_order.renumber(t, None, set())
        self.assertEqual(got.refused, [])
        self.assertEqual(got.moved, {1: 1, 2: 2, 3: 3})
        self.assertEqual([i.as_raw() for i in got.plan], PLAN3)


class OrderShapeTest(unittest.TestCase):
    """3. `--order` の形の誤り。"""

    def test_bad_orders_are_named(self):
        t = parsed(PLAN3, ["implement-feedback"])
        for text, word in (
            ("[", "JSON"),
            ("[]", "plan"),
            ('{"steps": {}}', "steps"),
            ('{"plan": []}', "plan"),
            ('{"plan": {"x": []}}', "x"),
            ('{"plan": {"4": []}}', "4"),
            ('{"plan": {"2": [2]}}', "自分"),
            ('{"plan": {"2": [9]}}', "9"),
            ('{"plan": {"2": [true]}}', "True"),
            ('{"plan": {"2": ["1"]}}', "'1'"),
            ('{"plan": {"2": 1}}', "リスト"),
            ('{"feedback": {"4": [1]}}', "1"),
            ('{"feedback": {"3": []}}', "3"),
        ):
            with self.subTest(order=text):
                order, why = plan_order.parse_order(text, t)
                self.assertIsNone(order)
                self.assertIn(word, why)

    def test_feedback_is_refused_when_the_proposal_has_none(self):
        order, why = plan_order.parse_order('{"feedback": {}}', parsed(PLAN3))
        self.assertIsNone(order)
        self.assertIn("feedback", why)

    def test_repeated_numbers_are_folded(self):
        order, why = plan_order.parse_order('{"plan": {"3": [1, 1, 2]}}', parsed(PLAN3))
        self.assertEqual(why, "")
        self.assertEqual(order["plan"][3], [1, 2])


class LockMovedTest(unittest.TestCase):
    """5. 延期の引き受け手は、元の項の同一性（振り直す前の番号）で比べる。"""

    def test_a_moved_deferred_item_is_compared_with_its_own_target(self):
        # 承認済み: 1 の延期は 3 が引き受ける。3 には子がある（固定）。
        current = parsed(
            [
                "research",
                {"type": "design", "review": "defer"},
                {"type": "implement", "after": [1, 2]},
            ]
        )
        # 振り直しで 1 と 2 が入れ替わった。引き受け手は同じ 3。
        revised = parsed(
            [
                {"type": "design", "review": "defer"},
                "research",
                {"type": "implement", "after": [1, 2]},
            ]
        )
        moved = {1: 2, 2: 1, 3: 3}
        with_moved = agree_candidates.lock_problems(revised, current, {3}, moved)
        self.assertFalse(any("延期" in p.detail for p in with_moved), with_moved)
        by_number = agree_candidates.lock_problems(revised, current, {3})
        self.assertTrue(any("延期" in p.detail for p in by_number), by_number)


class ApprovedTextTest(unittest.TestCase):
    """8. 承認を渡す文。"""

    def test_a_parent_with_a_plan_and_its_children_are_told_to_reread_the_numbers(self):
        parent = ticket_model.Ticket(ticket="i0001", plan=[ticket_model.PlanItem("design")])
        child = ticket_model.Ticket(ticket="i0001-01-01", parent="i0001", phase=1)
        for tickets in ([parent], [child]):
            with self.subTest(tickets=[t.ticket for t in tickets]):
                text = agree_screen.approved_text(tickets, set(), "/w")
                self.assertIn("番号は承認済みチケットで読み直す", text)

    def test_a_parent_without_a_plan_is_not_told(self):
        parent = ticket_model.Ticket(ticket="i0001")
        text = agree_screen.approved_text([parent], set(), "/w")
        self.assertNotIn("読み直す", text)


class PlanOrderHarness(PhaseHarness):
    def todo(self, name):
        return os.path.join(self.parent_tree, "wip", "proposals", "todo", name + ".md")

    def read(self, name="i0001"):
        with open(self.todo(name), encoding="utf-8") as f:
            return f.read()

    def order(self, *extra, name="i0001"):
        return self.ccnavi("--plan-order", name, *extra)

    def body(self, *extra, name="i0001"):
        result = self.order("--json", *extra, name=name)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def write_order(self, order, expect=None, *extra):
        if expect is None:
            expect = self.body()["source_sha"]
        return self.order(
            "--order", json.dumps(order), "--expect", expect, "--write", "--json", *extra
        )


class PlanOrderJsonTest(PlanOrderHarness):
    """2・3. 読むだけの答え。"""

    def test_json_reads_the_proposal_and_writes_nothing(self):
        text = parent_text("i0001", PLAN3)
        self.propose("i0001", text)
        body = self.body()
        self.assertEqual(body["ticket"], "i0001")
        self.assertRegex(body["source_sha"], r"^[0-9a-f]{64}$")
        self.assertEqual(body["problems"], [])
        self.assertEqual(body["loose"], [])
        self.assertEqual(body["refused"], [])
        self.assertEqual(body["revision_problems"], [])
        self.assertEqual(body["children"], [])
        self.assertTrue(body["writable"])
        [plan] = body["plans"]
        self.assertEqual(plan["ticket"], "i0001")
        self.assertEqual(plan["part"], "plan")
        self.assertEqual(plan["source_sha"], body["source_sha"])
        self.assertEqual(
            [(i["number"], i["from"], i["type"]) for i in plan["items"]],
            [(1, 1, "research"), (2, 2, "design"), (3, 3, "implement")],
        )
        self.assertEqual(plan["items"][0]["title"], "調査")
        self.assertEqual(plan["items"][0]["review"], "none")
        self.assertEqual(plan["items"][1]["review"], "mr")
        self.assertFalse(plan["items"][1]["deferred"])
        self.assertFalse(plan["items"][1]["locked"])
        self.assertEqual(plan["after"], {"2": [1], "3": [2]})
        self.assertEqual(plan["proposed"], {"2": [1], "3": [2]})
        self.assertIsNone(plan["current"])
        self.assertEqual(plan["loose"], [])
        self.assertEqual(plan["ready"], [1])
        self.assertEqual(plan["problems"], [])
        self.assertEqual(self.read(), text)

    def test_order_renumbers_and_answers_without_writing(self):
        text = parent_text(
            "i0001",
            [
                "research",
                {"type": "design", "review": "defer", "after": [1]},
                {"type": "implement", "after": [2]},
            ],
        )
        self.propose("i0001", text)
        body = self.body("--order", json.dumps({"plan": {"1": [], "2": [1, 3], "3": [1]}}))
        [plan] = body["plans"]
        self.assertEqual(
            [(i["number"], i["from"], i["type"], i["deferred"]) for i in plan["items"]],
            [(1, 1, "research", False), (2, 3, "implement", False), (3, 2, "design", True)],
        )
        self.assertEqual(plan["after"], {"2": [1], "3": [1, 2]})
        self.assertEqual(plan["proposed"], {"2": [1], "3": [2]})
        # 延期した design（3 番）を引き受ける項が無い（3 番が最後の項）。
        self.assertTrue(any("延期" in p for p in body["problems"]), body["problems"])
        self.assertFalse(body["writable"])
        self.assertEqual(self.read(), text)

    def test_loose_items_are_named(self):
        self.propose("i0001", parent_text("i0001", PLAN3))
        body = self.body("--order", json.dumps({"plan": {"2": [1]}}))
        self.assertEqual(body["loose"], [1, 2])
        self.assertEqual(body["plans"][0]["loose"], [1, 2])
        self.assertEqual(body["plans"][0]["ready"], [1, 3])
        self.assertTrue(any("待たない" in p for p in body["problems"]), body["problems"])

    def test_feedback_is_a_plan_of_its_own(self):
        self.propose(
            "i0001",
            parent_text(
                "i0001",
                ["design"],
                feedback=[
                    {"type": "implement-feedback", "after": []},
                    {"type": "chores-feedback", "after": []},
                    {"type": "implement-feedback", "after": [2, 3]},
                ],
            ),
        )
        body = self.body("--order", json.dumps({"feedback": {"2": [3], "3": [], "4": [2, 3]}}))
        plan, feedback = body["plans"]
        self.assertEqual(plan["part"], "plan")
        self.assertEqual(feedback["part"], "feedback")
        self.assertEqual(
            [(i["number"], i["from"]) for i in feedback["items"]], [(2, 3), (3, 2), (4, 4)]
        )
        self.assertEqual(feedback["after"], {"3": [2], "4": [2, 3]})

    def test_a_cycle_is_refused(self):
        self.propose("i0001", parent_text("i0001", PLAN3))
        body = self.body("--order", json.dumps({"plan": {"1": [3], "2": [1], "3": [2]}}))
        self.assertEqual([r["kind"] for r in body["refused"]], ["cycle"])
        self.assertTrue(body["refused"][0]["text"])
        self.assertFalse(body["writable"])
        # 断った線では振り直さない。答えは提案のまま。
        self.assertEqual([i["from"] for i in body["plans"][0]["items"]], [1, 2, 3])

    def test_a_malformed_order_is_a_usage_error(self):
        self.propose("i0001", parent_text("i0001", PLAN3))
        result = self.order("--json", "--order", '{"plan": {"2": [2]}}')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("--order", result.stderr)

    def test_flags_that_go_together(self):
        self.propose("i0001", parent_text("i0001", PLAN3, copy=None))
        for args, word in (
            (("--plan-order", "i0001"), "--json"),
            (("--plan-order", "i0001", "--order", "{}"), "--json"),
            (("--plan-order", "i0001", "--expect", "x", "--json"), "--write"),
            (("--plan-order", "i0001", "--write", "--expect", "x"), "--order"),
            (("--plan-order", "i0001", "--write", "--order", "{}"), "--expect"),
            (("--plan-order", "i0001", "--fill-phases", "--json"), "--fill-phases"),
            (("--order", "{}", "--json"), "--plan-order"),
            (("--write",), "--plan-order"),
            (("--agree", "--plan-order", "i0001", "--json"), "--agree"),
        ):
            with self.subTest(args=args):
                result = self.ccnavi(*args)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(word, result.stderr)

    def test_an_unknown_parent_is_named(self):
        result = self.order("--json", name="i0009")
        self.assertEqual(result.returncode, 1)
        self.assertIn("i0009", result.stderr)

    def test_the_source_sha_covers_the_pending_children(self):
        self.propose("i0001", parent_text("i0001", PLAN3))
        before = self.body()["source_sha"]
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/research/*"], False)
        )
        self.assertNotEqual(self.body()["source_sha"], before)

    def test_the_agent_may_type_plan_order(self):
        """7. `--agree` の外の独立したフラグなので、組み込みの止めに当たらない。"""
        rule = phase_forms.ticket_approval_rule("", self.root)
        order = json.dumps(REVERSE)
        for command in (
            "ccnavi --plan-order i0001 --json",
            f"ccnavi --plan-order i0001 --json --order '{order}'",
            f".ccnavi/bin/ccnavi --plan-order i0001 --order '{order}' --expect abc --write",
        ):
            with self.subTest(command=command):
                self.assertIsNone(rule.compiled.search(command))
                self.assertEqual(phase_forms.human_path_form(command), ("", ""))


class PlanOrderWriteTest(PlanOrderHarness):
    """4・6. 書き戻し。"""

    def test_write_changes_only_the_plan_value(self):
        text = parent_text("i0001", PLAN3).replace("title: 親\n", "# 題の前のコメント\ntitle: 親\n")
        self.propose("i0001", text)
        result = self.write_order(REVERSE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        written = self.read()
        front, before = front_of(written), front_of(text)
        self.assertEqual(
            front["plan"],
            ["design", {"type": "research", "after": [1]}, {"type": "implement", "after": [2]}],
        )
        self.assertEqual(
            {k: v for k, v in front.items() if k != "plan"},
            {k: v for k, v in before.items() if k != "plan"},
        )
        self.assertIn("# 題の前のコメント\ntitle: 親\n", written)
        self.assertEqual(written.split("---\n", 2)[2], text.split("---\n", 2)[2])
        # 書いたあとのハッシュを返す。読み直した答えと同じ。
        answer = json.loads(result.stdout)
        self.assertTrue(answer["written"])
        self.assertEqual(answer["source_sha"], self.body()["source_sha"])
        # 書いた順序が答えの番号どおり。もう一度読めば番号は動かない。
        again = self.body()
        self.assertEqual([i["from"] for i in again["plans"][0]["items"]], [1, 2, 3])
        self.assertEqual(again["plans"][0]["after"], {"2": [1], "3": [2]})

    def test_writing_the_same_order_changes_nothing(self):
        text = parent_text("i0001", PLAN3)
        self.propose("i0001", text)
        result = self.write_order({"plan": {"1": [], "2": [1], "3": [2]}})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(json.loads(result.stdout)["written"])
        self.assertEqual(self.read(), text)

    def test_a_stale_expect_writes_nothing(self):
        text = parent_text("i0001", PLAN3)
        self.propose("i0001", text)
        result = self.write_order(REVERSE, expect="0" * 64)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("外で変わった", result.stderr)
        self.assertFalse(json.loads(result.stdout)["written"])
        self.assertEqual(self.read(), text)

    def test_an_order_error_writes_nothing(self):
        text = parent_text("i0001", PLAN3)
        self.propose("i0001", text)
        result = self.write_order({"plan": {"2": [1]}})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("順序", result.stderr)
        self.assertEqual(self.read(), text)

    def test_a_refused_line_writes_nothing(self):
        text = parent_text("i0001", PLAN3)
        self.propose("i0001", text)
        result = self.write_order({"plan": {"1": [3], "2": [1], "3": [2]}})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.read(), text)

    def test_nothing_is_written_when_other_fields_would_change(self):
        """`plan:` の値にアンカーがあり、ほかの欄が別名で指していれば書かない。"""
        text = parent_text("i0001", PLAN3)
        head, rest = text.split("plan:\n", 1)
        items, tail = rest.split("human_review:", 1)
        text = head + "plan: &steps\n" + items + "rationale_extra: *steps\nhuman_review:" + tail
        self.propose("i0001", text)
        result = self.write_order(REVERSE)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("書かない", result.stderr)
        self.assertEqual(self.read(), text)

    def test_the_written_proposal_is_approved_and_the_old_digest_is_not(self):
        self.propose("i0001", parent_text("i0001", PLAN3))
        self.commit_parent()
        old = json.loads(self.ccnavi("--agree", "--preview", "--json").stdout)["digest"]
        result = self.write_order(REVERSE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.commit_parent("reorder")
        stale = self.ccnavi("--agree", "--yes", "i0001", "--digest", old, "--json")
        self.assertEqual(stale.returncode, 1, stale.stdout + stale.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.approved, "doing", "i0001.md")))
        fresh = json.loads(self.ccnavi("--agree", "--preview", "--json").stdout)["digest"]
        done = self.ccnavi("--agree", "--yes", "i0001", "--digest", fresh, "--json")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        with open(os.path.join(self.approved, "doing", "i0001.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), self.read())

    def test_preview_plans_come_from_the_same_function(self):
        self.propose("i0001", parent_text("i0001", PLAN3))
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/research/*"], False)
        )
        self.commit_parent()
        preview = json.loads(self.ccnavi("--agree", "--preview", "--json").stdout)
        self.assertEqual(preview["plans"], self.body()["plans"])


class PlanOrderRevisionTest(PlanOrderHarness):
    """5. 改版では子が承認された番号を動かさない。"""

    PLAN = [
        "research",
        {"type": "design", "after": [1]},
        {"type": "acceptance", "after": [1]},
        {"type": "implement", "after": [2, 3]},
    ]
    REVISED = [
        "research",
        {"type": "design", "after": [1]},
        {"type": "acceptance", "after": [1]},
        {"type": "implement", "after": [2, 3, 5]},
        {"type": "chores", "after": [1]},
    ]

    def setUp(self):
        super().setUp()
        self.family(plan=self.PLAN)
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/research/*"], False)
        )
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.propose("i0001", parent_text("i0001", self.REVISED))
        self.commit_parent("revise")

    def test_a_fixed_number_stays_and_free_ones_are_packed(self):
        order = {"plan": {"1": [], "2": [1, 5], "3": [1], "4": [2, 3, 5], "5": [1]}}
        body = self.body("--order", json.dumps(order))
        self.assertEqual(body["refused"], [])
        [plan] = body["plans"]
        self.assertEqual([i["from"] for i in plan["items"]], [1, 3, 5, 2, 4])
        self.assertEqual([i["locked"] for i in plan["items"]], [True] + [False] * 4)
        self.assertEqual(plan["current"], {"2": [1], "3": [1], "4": [2, 3]})
        self.assertEqual(body["revision_problems"], [])
        self.assertTrue(body["writable"])
        result = self.write_order(order)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            [i if isinstance(i, str) else i["type"] for i in front_of(self.read())["plan"]],
            ["research", "acceptance", "chores", "design", "implement"],
        )
        self.commit_parent("reorder")
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)

    def test_a_line_into_a_fixed_number_is_refused(self):
        order = {"plan": {"1": [5], "2": [1], "3": [1], "4": [2, 3, 5], "5": []}}
        body = self.body("--order", json.dumps(order))
        self.assertEqual([r["kind"] for r in body["refused"]], ["locked"])
        self.assertEqual(body["refused"][0]["number"], 1)
        before = self.read()
        self.assertEqual(self.write_order(order).returncode, 1)
        self.assertEqual(self.read(), before)


class PlanOrderUnpackableTest(PlanOrderHarness):
    """5. 固定した番号を動かさずに番号が振れなければ「詰められない」。"""

    def test_an_unpackable_line_is_refused(self):
        self.family(plan=["research", {"type": "design", "after": [1]}])
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/research/*"], False)
        )
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        # 改版で 3 番を足す。2 番が 3 番を待つ線は 2 番を後ろへ回すが、固定した 1 番の後ろに
        # 置ける項が 3 番しか無く、3 番は 2 番の前に要る。並べ替えれば振れるので詰められる。
        self.propose(
            "i0001",
            parent_text(
                "i0001",
                [
                    "research",
                    {"type": "design", "after": [1]},
                    {"type": "acceptance", "after": [1, 2]},
                ],
            ),
        )
        self.commit_parent("revise")
        packed = self.body("--order", json.dumps({"plan": {"2": [1, 3], "3": [1]}}))
        self.assertEqual(packed["refused"], [])
        # 固定した 1 番が 2 番を待つ線は、子が承認された番号へ入る線なので断る。
        locked = self.body("--order", json.dumps({"plan": {"1": [2], "3": [1, 2]}}))
        self.assertEqual([r["kind"] for r in locked["refused"]], ["locked"])
        # 詰められない形は見本の表（tests/fixtures/plan-reorder.json）が見る。
        t = parsed(
            [
                "research",
                {"type": "design", "after": [1]},
                {"type": "acceptance", "after": [1]},
                {"type": "implement", "after": [2, 3]},
            ]
        )
        order, _ = plan_order.parse_order('{"plan": {"2": [1, 3], "3": [1], "4": [2, 3]}}', t)
        got = plan_order.renumber(t, order, {1, 3})
        self.assertEqual([r.kind for r in got.refused], ["unpackable"])
        self.assertIn("詰められない", got.refused[0].text)


class PlanOrderChildrenTest(PlanOrderHarness):
    """子の提案の一覧（書き換える子は、番号の変わる項を指す `todo/` の子）。"""

    def test_children_whose_number_moves_are_listed(self):
        self.propose("i0001", parent_text("i0001", PLAN3))
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/research/*"], False)
        )
        self.propose("i0001-03-01", child_text("i0001-03-01", "i0001", 3, ["src/*"]))
        body = self.body("--order", json.dumps(REVERSE))
        self.assertEqual(
            [(c["ticket"], c["phase"], c["new_phase"]) for c in body["children"]],
            [("i0001-01-01", 1, 2)],
        )
        self.assertTrue(body["children"][0]["path"].endswith("i0001-01-01.md"))


if __name__ == "__main__":
    unittest.main()
