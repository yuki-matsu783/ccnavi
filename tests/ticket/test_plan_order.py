"""計画の順序を振り直して書き戻す `ccnavi --plan-order <親>`の受入テスト。

VS Code の計画編集タブが打つ経路。タブは図で引いた線（元の番号での直接の先行）を
`--order` に付けて実行ファイルに渡し、実行ファイルが番号を振り直して答える。保存では
`--write --expect <ハッシュ>` で提案の計画を書き換える。見るのは 5 つ。

1. `--plan-order <親> --json` は読むだけ。`--order` を付ければ振り直した答えを返す
2. `--order` の形の誤りは使い方の誤り。循環・子が承認された番号へ入る線・詰められない線は断る
3. `--write` は親の提案の `plan:` / `feedback:` の値だけを書き換え、ほかのバイトを変えない。
   `--expect` が今の中身と違う・順序の検査に error がある・断った線なら何も書かない
4. 改版では子が承認された番号を動かさず、改版の錠に落ちる形は書かない
5. 書き換えたあとの一覧で承認が通り、書き換えの前のダイジェストでは止まる。`--agree --preview
   --json` の `plans` は `--plan-order <親> --json` と同じ関数で作る

番号の振り直しの決まり（拡張と共有する見本の表 tests/fixtures/plan-reorder.json）と、承認を渡す文は
go/internal/tickets/proposal の planedit と agree が見る。
"""

from __future__ import annotations

import json
import os
import unittest

import yaml

from tests.ticket.test_board import _portable
from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import ROOT, write
from tests.ticketsample import child_text, parent_text

# VS Code 拡張の計画編集タブが読む `--plan-order` の答えの実例。
PLAN_ORDER_FIXTURE = os.path.join(
    ROOT, "extensions", "vscode", "ccnavi-board", "test", "fixtures", "plan-order.json"
)

# 3 ステップの一直線。research → design → implement。
PLAN3 = ["research", {"type": "design", "after": [1]}, {"type": "implement", "after": [2]}]
# design が research に先行するように線を逆に引いた形（元の番号での直接の先行）。
REVERSE = {"plan": {"1": [2], "2": [], "3": [1]}}


def front_of(text: str) -> dict:
    return yaml.safe_load(text.split("---\n", 2)[1])


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

    def test_order_renumbers_and_answers_without_writing(self):
        text = parent_text(
            "i0001",
            [
                "research",
                {"type": "design", "deferred": True, "after": [1]},
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
        # 延期した design（3 番）を引き受けるステップが無い（3 番が最後のステップ）。
        self.assertTrue(any("延期" in p for p in body["problems"]), body["problems"])
        self.assertFalse(body["writable"])
        self.assertEqual(self.read(), text)

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
            (("user", "agree", "--plan-order", "i0001", "--json"), "--agree"),
        ):
            with self.subTest(args=args):
                result = self.ccnavi(*args)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(word, result.stderr)

    def test_the_source_sha_covers_the_pending_children(self):
        result = self.order("--json", name="i0009")
        self.assertEqual(result.returncode, 1)
        self.assertIn("i0009", result.stderr)
        self.propose("i0001", parent_text("i0001", PLAN3))
        before = self.body()["source_sha"]
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/research/*"], False)
        )
        self.assertNotEqual(self.body()["source_sha"], before)


class PlanOrderAfterErrorsTest(PlanOrderHarness):
    """提案に元からある `after` の形の誤りは、振り直しても検査から消えない。"""

    def test_after_errors_in_the_proposal_are_kept(self):
        plan = [PLAN3[0], PLAN3[1], {"type": "implement", "after": [2, "x", 7]}]
        self.propose("i0001", parent_text("i0001", plan))
        body = self.body()
        self.assertTrue(body["problems"], body)
        self.assertTrue(body["plans"][0]["problems"], body)
        self.assertFalse(body["writable"])
        reordered = self.body("--order", json.dumps(REVERSE))
        self.assertTrue(reordered["problems"], reordered)
        self.assertFalse(reordered["writable"])
        # 渡さなかった部分（feedback）の after の誤りも残る
        text = parent_text(
            "i0001", PLAN3, feedback=[{"type": "implement-feedback", "after": ["zz"]}]
        )
        self.propose("i0001", text)
        body = self.body("--order", json.dumps(REVERSE))
        self.assertTrue(any("zz" in p for p in body["problems"]), body["problems"])
        self.assertFalse(body["writable"])
        result = self.write_order(REVERSE)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.read(), text)


class PlanOrderWriteTest(PlanOrderHarness):
    """4・6. 書き戻し。"""

    def test_nothing_is_written_when_the_order_or_the_other_fields_are_wrong(self):
        text = parent_text("i0001", PLAN3)
        self.propose("i0001", text)
        # 終端が 1 つでない順序
        result = self.write_order({"plan": {"2": [1]}})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("順序", result.stderr)
        self.assertEqual(self.read(), text)
        # 断った線（循環）
        result = self.write_order({"plan": {"1": [3], "2": [1], "3": [2]}})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.read(), text)
        # `plan:` の値にアンカーがあり、ほかの欄が別名で指していれば書かない
        head, rest = text.split("plan:\n", 1)
        items, tail = rest.split("human_review:", 1)
        anchored = head + "plan: &steps\n" + items + "rationale_extra: *steps\nhuman_review:" + tail
        self.propose("i0001", anchored)
        result = self.write_order(REVERSE)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("何も反映しません", result.stderr)
        self.assertEqual(self.read(), anchored)

    def test_the_written_proposal_is_approved_and_the_old_digest_is_not(self):
        self.propose("i0001", parent_text("i0001", PLAN3))
        self.commit_parent()
        old = json.loads(self.ccnavi("--agree", "--preview", "--json").stdout)["digest"]
        result = self.write_order(REVERSE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        written = self.read()
        self.commit_parent("reorder")
        stale = self.ccnavi("user", "agree", "--yes", "i0001", "--digest", old, "--json")
        self.assertEqual(stale.returncode, 1, stale.stdout + stale.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.approved, "doing", "i0001.md")))
        fresh = json.loads(self.ccnavi("--agree", "--preview", "--json").stdout)["digest"]
        done = self.ccnavi("user", "agree", "--yes", "i0001", "--digest", fresh, "--json")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        with open(os.path.join(self.approved, "doing", "i0001.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), written)

    def test_an_agent_rewriting_the_order_after_the_save_changes_the_preview_sha(self):
        """保存したあとにエージェントが `after` を書き戻すと、preview の `source_sha` が変わる。

        拡張は保存で返った `source_sha` を覚え、承認のオーバーレイを開くときに preview の
        `plans` の `source_sha` と比べて、違えば「保存したあとに提案が変わった」と出す。
        その比べ方が成り立つ前提（書き換えなければ同じ・書き戻せば違う、図は書き戻した順序）を見る。
        """
        text = parent_text("i0001", PLAN3)
        self.propose("i0001", text)
        self.commit_parent()
        result = self.write_order(REVERSE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        saved = json.loads(result.stdout)["source_sha"]
        self.commit_parent("reorder")
        preview = json.loads(self.ccnavi("--agree", "--preview", "--json").stdout)
        self.assertEqual([p["source_sha"] for p in preview["plans"]], [saved])
        # エージェントが提案を書いた順序へ黙って戻す。
        self.propose("i0001", text)
        self.commit_parent("revert")
        preview = json.loads(self.ccnavi("--agree", "--preview", "--json").stdout)
        self.assertNotEqual(preview["plans"][0]["source_sha"], saved)
        self.assertEqual(preview["plans"][0]["after"], {"2": [1], "3": [2]})

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
        {"type": "chores", "after": [1]},
        {"type": "implement", "after": [2, 3, 4]},
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
        order = {"plan": {"1": [], "2": [1, 4], "3": [1], "4": [1], "5": [2, 3, 4]}}
        body = self.body("--order", json.dumps(order))
        self.assertEqual(body["refused"], [])
        [plan] = body["plans"]
        self.assertEqual([i["from"] for i in plan["items"]], [1, 3, 4, 2, 5])
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

        # 詰められない形は見本の表（tests/fixtures/plan-reorder.json）を Go の planedit が見る。


def with_predecessors(text: str, preds: list[str]) -> str:
    """子の本文に `predecessors:` を足す。"""
    return text.replace("human_review:", f"predecessors: [{', '.join(preds)}]\nhuman_review:", 1)


class PlanOrderChildrenTest(PlanOrderHarness):
    """子の提案の追従。番号の変わるステップを指す `todo/` の子は、識別子・ファイル名・`ticket:`・
    `phase:` を付け替え、それを指すほかの子の `predecessors` も直す。承認済みの側には触れない。"""

    def setUp(self):
        super().setUp()
        self.propose("i0001", parent_text("i0001", PLAN3))
        self.a = child_text("i0001-01-01", "i0001", 1, ["wip/research/*"], False)
        self.b = child_text("i0001-02-01", "i0001", 2, ["wip/design/*"])
        self.c = with_predecessors(
            child_text("i0001-03-01", "i0001", 3, ["src/*"]), ["i0001-01-01", "i0001-02-01"]
        )
        self.propose("i0001-01-01", self.a)
        self.propose("i0001-02-01", self.b)
        self.propose("i0001-03-01", self.c)

    def names(self):
        return sorted(os.listdir(os.path.dirname(self.todo("i0001"))))

    def snapshot(self):
        here = os.path.dirname(self.todo("i0001"))
        out = {}
        for name in os.listdir(here):
            with open(os.path.join(here, name), "rb") as f:
                out[name] = f.read()
        return out

    def assert_blocked(self, word):
        """付け替えを止める事情があるとき、読むだけの答えが理由を言い、書いても何も変わらない。"""
        before = self.snapshot()
        body = self.body("--order", json.dumps(REVERSE))
        self.assertTrue(any(word in p for p in body["child_problems"]), body)
        self.assertFalse(body["writable"])
        self.assertEqual(self.write_order(REVERSE).returncode, 1)
        self.assertEqual(self.snapshot(), before)

    def test_what_blocks_the_move_writes_nothing(self):
        todo = os.path.dirname(self.todo("i0001"))
        wip = os.path.dirname(todo)
        # 付け替え先の名前に、動かす子に数えないファイル（読めない提案）がある
        saved = self.read("i0001-02-01")
        write(self.todo("i0001-02-01"), "---\n: :\n")
        self.assert_blocked("i0001-02-01")
        write(self.todo("i0001-02-01"), saved)
        # 承認済みの側（フローの下書き）に参照がある
        flow = os.path.join(self.approved, "flows", "i0001-01-01.yml")
        write(flow, "nodes: []\n")
        self.assert_blocked("flows")
        os.remove(flow)
        # 承認済みチケットが付け替える子を指している
        held = with_predecessors(child_text("i0002-01-01", "i0002", 1, ["src/*"]), ["i0001-01-01"])
        approved = os.path.join(self.approved, "doing", "i0002-01-01.md")
        write(approved, held)
        self.assert_blocked("承認済みチケット")
        os.remove(approved)
        # review/ の提案が付け替える子を指している
        review = os.path.join(wip, "review", "i0002-01-01.md")
        write(review, held)
        self.assert_blocked("review/")
        os.remove(review)
        # 付け替え先の名前のワークツリーがある
        self.worktree("i0001-02-01", "main")
        self.assert_blocked("ワークツリー")

    def test_a_failure_part_way_puts_everything_back(self):
        """子の提案を書いたあとで落ちたら（下書きの付け替え先がディレクトリで塞がれている）、
        書いたものを全部戻す。"""
        drafts = os.path.join(os.path.dirname(os.path.dirname(self.todo("i0001"))), "flows")
        os.makedirs(os.path.join(drafts, "i0001-02-01.yml"))
        write(os.path.join(drafts, "i0001-02-01.yml", "keep"), "k\n")
        write(os.path.join(drafts, "i0001-01-01.yml"), "nodes: [a]\n")
        before = self.snapshot()
        result = self.write_order(REVERSE)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("書いたものは戻した", result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), before)
        with open(os.path.join(drafts, "i0001-01-01.yml"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "nodes: [a]\n")
        self.assertTrue(os.path.isfile(os.path.join(drafts, "i0001-02-01.yml", "keep")))

    def test_a_child_whose_other_fields_would_change_writes_nothing(self):
        """`phase:` の値のアンカーを、ほかの欄が別名で指していれば書かない。"""
        anchored = self.a.replace("phase: 1\n", "phase: &p 1\nextra_b: *p\n", 1)
        self.propose("i0001-01-01", anchored)
        self.assertEqual(front_of(anchored)["extra_b"], 1)
        before = self.snapshot()
        result = self.write_order(REVERSE)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_a_child_pointing_outside_the_plan_is_reported(self):
        self.propose("i0001-05-01", child_text("i0001-05-01", "i0001", 5, ["src/*"]))
        body = self.body()
        self.assertEqual([m["ticket"] for m in body["mismatched"]], ["i0001-05-01"])
        self.assertEqual(body["mismatched"][0]["phase"], 5)
        self.assertTrue(body["mismatched"][0]["text"])


class PlanOrderApprovedChildTest(PlanOrderHarness):
    """承認済みの子には触れない。動くのは承認前の子の提案だけ。"""

    def test_an_approved_child_stays_and_a_pending_one_moves(self):
        self.family(plan=PlanOrderRevisionTest.PLAN)
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/research/*"], False)
        )
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        approved = os.path.join(self.approved, "doing", "i0001-01-01.md")
        with open(approved, "rb") as f:
            held = f.read()
        self.propose("i0001", parent_text("i0001", PlanOrderRevisionTest.REVISED))
        self.propose("i0001-02-01", child_text("i0001-02-01", "i0001", 2, ["wip/design/*"]))
        self.commit_parent("revise")
        order = {"plan": {"1": [], "2": [1, 4], "3": [1], "4": [1], "5": [2, 3, 4]}}
        body = self.body("--order", json.dumps(order))
        self.assertEqual(
            [(c["ticket"], c["new_ticket"]) for c in body["children"]],
            [("i0001-02-01", "i0001-04-01")],
        )
        result = self.write_order(order)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(front_of(self.read("i0001-04-01"))["phase"], 4)
        self.assertFalse(os.path.exists(self.todo("i0001-02-01")))
        with open(approved, "rb") as f:
            self.assertEqual(f.read(), held)


class PlanOrderFixtureTest(PlanOrderHarness):
    """拡張のフィクスチャ（extensions/vscode/ccnavi-board/test/fixtures/plan-order.json）と同じ形。

    計画編集タブが読む答えを並べる。開いたとき（`open`）、逆向きの線で番号と子が
    動くとき（`reverse`）、循環で断るとき（`cycle`）、終端の検査で書けないとき（`loose`）、
    改版で子が承認された番号があるとき（`revision`）、書いたとき（`written`）。形を変えたら
    `CCNAVI_BOARD_FIXTURE=1` を付けてこのテストを走らせ、フィクスチャを書き直す。
    """

    PLAN = [
        "research",
        {"type": "design", "after": [1]},
        {"type": "implement", "after": [1]},
        {"type": "docs", "after": [2, 3]},
    ]
    FEEDBACK = ["design-feedback", {"type": "implement-feedback", "after": [5]}]
    REVERSE = {"plan": {"1": [], "2": [1, 3], "3": [1], "4": [2]}}
    # 走らせるたびに変わる値（一時ディレクトリのパスを含むハッシュ）。形だけ確かめて置き換える。
    SHA = "<sha>"

    def mask(self, body, root):
        def walk(value):
            if isinstance(value, dict):
                return {
                    k: (self.masked_sha(v) if k == "source_sha" else walk(v))
                    for k, v in value.items()
                }
            if isinstance(value, list):
                return [walk(v) for v in value]
            return value

        return walk(_portable(body, root))

    def masked_sha(self, value):
        self.assertRegex(value, r"^[0-9a-f]{64}$")
        return self.SHA

    def test_shapes_match_the_extension_fixture(self):
        self.propose("i0001", parent_text("i0001", self.PLAN, feedback=self.FEEDBACK))
        self.propose("i0001-02-01", child_text("i0001-02-01", "i0001", 2, ["wip/design/*"]))
        self.propose("i0001-03-01", child_text("i0001-03-01", "i0001", 3, ["src/*"]))
        bodies = {
            "open": self.body(),
            "reverse": self.body("--order", json.dumps(self.REVERSE)),
            "cycle": self.body(
                "--order", json.dumps({"plan": {"1": [4], "2": [1], "3": [1], "4": [2, 3]}})
            ),
            "loose": self.body("--order", json.dumps({"plan": {"2": [1], "3": [1], "4": [2]}})),
        }
        self.assertTrue(bodies["reverse"]["children"])
        self.assertEqual([r["kind"] for r in bodies["cycle"]["refused"]], ["cycle"])
        self.assertFalse(bodies["loose"]["writable"])
        result = self.write_order(self.REVERSE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        bodies["written"] = json.loads(result.stdout)
        self.assertTrue(bodies["written"]["written"])
        portable = {name: self.mask(body, self.root) for name, body in bodies.items()}
        portable["revision"] = self.revision()
        if os.environ.get("CCNAVI_BOARD_FIXTURE"):
            write(PLAN_ORDER_FIXTURE, json.dumps(portable, ensure_ascii=False, indent=1) + "\n")
        with open(PLAN_ORDER_FIXTURE, encoding="utf-8") as f:
            fixture = json.load(f)
        self.assertEqual(sorted(fixture), sorted(portable))
        for name in sorted(portable):
            self.assertEqual(fixture[name], portable[name], name)

    def revision(self):
        """改版の答え（1 番に子が承認されている＝固定した番号）。改版の道具を別に組んで読む。"""

        class Revision(PlanOrderRevisionTest):
            def runTest(self):
                pass

        harness = Revision()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        order = {"plan": {"1": [], "2": [1, 4], "3": [1], "4": [1], "5": [2, 3, 4]}}
        return self.mask(harness.body("--order", json.dumps(order)), harness.root)


if __name__ == "__main__":
    unittest.main()
