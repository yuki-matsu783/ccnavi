"""全体計画の順序を計画の項の `after` で決める（設計 9.7）の受入テスト。

見るのは 9 つ。

1. フェーズ定義は順序を持たない。`phases.yml` に残った `order`・`after`・`overlap`・`requires` は
   読まずに通し、warn で言う
2. 項の `after` の読み方。形の誤りは読み込みで落とさず、例外も出さない
3. 待ち方は項の `after` を推移的に辿って計算する。書かない項は何も待たない。
   フィードバック計画はいまは一直線（前の番号を全部待つ）で読む
4. 計画の検査（終端・最後の項の延期・延期の引き受け手とそのレビュー）
5. 承認と順序。並行の枝が一緒に始まり、合流点は両方を待つ
6. 待ち方のファイルは無い。承認済みチケットの計画から都度計算し、残ったファイルは読まない
7. 手で動かした承認も `after` で読む。壊れた計画の親は判定で止まり、子も止まる
8. 改版の見つけ方（定義・`review`・推移的な待ちの並び）と、番号ごとの錠
9. 受け入れはそのフェーズと、それを待つ番号にだけ当てはまる。次の案内は並行の枝を挙げる
"""

from __future__ import annotations

import json
import os
import unittest

from ccnavi.infra import settings
from ccnavi.tickets import (
    agree_candidates,
    approval,
    approval_checks,
    approval_marks,
    phasetypes,
    ticket_model,
    workflow,
)
from ccnavi.tickets import ticket as ticket_mod
from tests import config_path
from tests.ticket.test_phases import PhaseHarness, child_text, parent_text
from tests.ticket.test_ticket import ROOT, write

# 順序の欄を持たない定義。順序は計画の項の `after` が決める。
DEFS = """
version: 1
phases:
  design: {title: 設計, review: none, scope: ["wip/*"]}
  acceptance: {title: 受入, review: none, scope: ["tests/*"]}
  implement: {title: 実装, review: none, scope: ["src/*"]}
  docs: {title: 文書, review: mr, scope: inherit}
  extra: {title: 追加, review: none, scope: ["wip/*"]}
  fixup: {kind: feedback, title: 対応, review: mr, scope: inherit}
"""

# 設計 → 受入と実装が並行 → 文書が両方を受ける（終端）。
PLAN = [
    "design",
    {"type": "acceptance", "after": [1]},
    {"type": "implement", "after": [1]},
    {"type": "docs", "after": [2, 3]},
]

# 計画の待ち方の見本の表。待ち方の決まりが黙って変わらないように、ここで固定する。
PLAN_WAITS = os.path.join(ROOT, "tests", "fixtures", "plan-waits.json")


def item(kind, review="", after=()):
    return ticket_model.PlanItem(type=kind, review=review, after=list(after))


def parent_of(plan, feedback=None):
    return ticket_model.Ticket(ticket="i0001", plan=list(plan), feedback=feedback)


def parsed(plan_yaml: str, feedback_yaml: str = ""):
    """計画の YAML を持つ親を読む。読み込みが落ちないことも見る。"""
    text = (
        "---\nversion: 1\nticket: i0001\n"
        + plan_yaml
        + feedback_yaml
        + 'title: 親\nallow:\n  - match: Write|Edit\n    glob: "src/*"\n---\n本文\n'
    )
    t, problems = ticket_mod.parse(text)
    assert t is not None, problems
    return t


class TypesTest(unittest.TestCase):
    def test_definitions_carry_no_order(self):
        types, problems = phasetypes.parse(DEFS)
        self.assertIsNotNone(types, problems)
        self.assertEqual([p for p in problems if p.severity == "error"], [])
        self.assertFalse(hasattr(types, "order"))
        self.assertFalse(hasattr(types["docs"], "after"))

    def test_old_relation_fields_are_left_unread_with_a_warning(self):
        """古い欄は error にしない。更新した直後に全部の承認と `--lint` が止まらないように。"""
        text = (
            "version: 1\norder: dag\nphases:\n"
            "  design: {title: 設計, review: none, overlap: [docs]}\n"
            "  docs: {title: 文書, review: mr, after: [design], requires: [design]}\n"
        )
        types, problems = phasetypes.parse(text)
        self.assertIsNotNone(types, problems)
        warned = [p.detail for p in problems if p.severity == "warn"]
        for name in ("order", "after", "overlap", "requires"):
            self.assertTrue(any(f"`{name}`" in d and "読まない" in d for d in warned), warned)
        self.assertTrue(all("after" in d for d in warned if "順序" in d), warned)
        self.assertEqual([p for p in problems if p.severity == "error"], [])

    def test_old_after_pointing_nowhere_is_not_an_error(self):
        types, problems = phasetypes.parse(
            "version: 1\nphases:\n  docs: {title: 文書, after: [nope, docs]}\n"
        )
        self.assertIsNotNone(types, problems)
        self.assertEqual([p for p in problems if p.severity == "error"], [])


class AfterReadingTest(unittest.TestCase):
    def test_after_is_read_as_numbers(self):
        t = parsed("plan:\n  - design\n  - {type: docs, after: [1]}\n")
        self.assertEqual(t.plan[1].after, [1])
        self.assertEqual(t.plan[0].after, [])
        self.assertEqual(t.plan[1].after_errors, [])

    def test_repeated_numbers_are_folded_and_warned(self):
        t = parsed("plan:\n  - design\n  - extra\n  - {type: docs, after: [2, 1, 2]}\n")
        self.assertEqual(t.plan[2].after, [1, 2])
        warned = [p.detail for p in workflow.problems(t) if p.severity == "warn"]
        self.assertTrue(any("2 度" in d for d in warned), warned)

    def test_broken_after_does_not_stop_reading_or_raise(self):
        """どの崩し方でも、読み・計算・検査・一覧が例外を出さず、誤りは error で言う。"""
        broken = [
            "2",
            '"2"',
            '["2"]',
            "[1.5]",
            "[[1]]",
            "[0]",
            "[-1]",
            "[3]",
            "[4]",
            "[99999999999999999999999]",
            "[true]",
            "{a: 1}",
            "null",
        ]
        for value in broken:
            with self.subTest(value=value):
                t = parsed(f"plan:\n  - design\n  - extra\n  - {{type: docs, after: {value}}}\n")
                if value == "null":
                    self.assertEqual(t.plan[2].after, [])
                    continue
                self.assertEqual(t.plan[2].after, [], value)
                self.assertTrue(t.plan[2].after_errors, value)
                wf = workflow.compute(t)
                self.assertEqual(wf.waits[3], [])
                self.assertIsNone(t.review_at(9))
                errors = [p.detail for p in workflow.problems(t) if p.severity == "error"]
                self.assertTrue(any("`plan[2]` の `after`" in d for d in errors), errors)
                workflow.lines(t, wf)

    def test_a_bool_is_not_a_number(self):
        t = parsed("plan:\n  - design\n  - {type: docs, after: [true]}\n")
        self.assertEqual(t.plan[1].after, [])
        self.assertTrue(t.plan[1].after_errors)

    def test_feedback_after_points_only_at_feedback_numbers(self):
        t = parsed(
            "plan:\n  - design\n  - {type: docs, after: [1]}\n",
            "feedback:\n  - fixup\n  - {type: fixup, after: [2]}\n",
        )
        self.assertEqual(t.feedback[1].after, [])
        errors = [p.detail for p in workflow.problems(t) if p.severity == "error"]
        self.assertTrue(any("`feedback[1]` の `after`" in d for d in errors), errors)
        good = parsed(
            "plan:\n  - design\n  - {type: docs, after: [1]}\n",
            "feedback:\n  - fixup\n  - {type: fixup, after: [3]}\n",
        )
        self.assertEqual(good.feedback[1].after, [3])

    def test_a_deferred_last_item_is_read_and_refused_by_the_check(self):
        """最後の項の延期は読む段では落とさない（落とすと承認済みチケットが索引に入らない）。"""
        t = parsed("plan:\n  - design\n  - {type: docs, review: defer, after: [1]}\n")
        self.assertTrue(t.plan[1].deferred)
        errors = [p.detail for p in workflow.problems(t) if p.severity == "error"]
        self.assertTrue(any("最後の項は延期できない" in d for d in errors), errors)

    def test_after_is_written_back_by_as_raw_and_not_compared_by_eq(self):
        self.assertEqual(item("docs", after=[1]).as_raw(), {"type": "docs", "after": [1]})
        self.assertEqual(item("docs").as_raw(), "docs")
        self.assertEqual(item("docs", after=[1]), item("docs", after=[2]))


class ComputeTest(unittest.TestCase):
    def test_waits_follow_after_transitively(self):
        wf = workflow.compute(
            parent_of(
                [
                    item("design"),
                    item("acceptance", after=[1]),
                    item("implement", after=[1]),
                    item("docs", after=[2, 3]),
                ]
            )
        )
        self.assertEqual(wf.order, ticket_model.WORKFLOW_DAG)
        self.assertEqual(wf.waits, {1: [], 2: [1], 3: [1], 4: [1, 2, 3]})

    def test_an_item_without_after_waits_for_nothing_even_for_the_same_type(self):
        wf = workflow.compute(parent_of([item("implement"), item("implement")]))
        self.assertEqual(wf.waits, {1: [], 2: []})

    def test_copying_the_transitive_waits_into_after_means_the_same(self):
        direct = parent_of([item("a"), item("b", after=[1]), item("c", after=[2])])
        copied = parent_of([item("a"), item("b", after=[1]), item("c", after=[1, 2])])
        self.assertEqual(workflow.compute(direct).waits, workflow.compute(copied).waits)

    def test_defer_goes_to_the_smallest_later_item_that_waits(self):
        parent = parent_of(
            [
                item("design"),
                item("acceptance", "defer", [1]),
                item("implement", after=[1]),
                item("docs", after=[2, 3]),
            ]
        )
        # 3（実装）は 2 を待たないので引き受けない。4（文書）が引き受ける。
        self.assertEqual(workflow.compute(parent).review_at, {2: 4})
        self.assertEqual(parent.review_at(2), 2) if parent.workflow else None
        parent.workflow = workflow.compute(parent)
        self.assertEqual(parent.review_at(2), 4)
        self.assertEqual(parent.covered_by(4), [2])

    def test_feedback_is_read_one_after_another_for_now(self):
        """フィードバック計画は全体計画の番号を全部と、前のフィードバックの番号を全部待つ。"""
        parent = parent_of(
            [item("design"), item("docs", after=[1])], [item("fixup"), item("fixup")]
        )
        wf = workflow.compute(parent)
        self.assertEqual(wf.waits[3], [1, 2])
        self.assertEqual(wf.waits[4], [1, 2, 3])
        parent.workflow = wf
        self.assertEqual(workflow.waits_of(parent, 4), [1, 2, 3])

    def test_a_deferred_feedback_item_goes_to_the_next_one(self):
        parent = parent_of(
            [item("design"), item("docs", after=[1])],
            [item("fixup", "defer"), item("fixup", "defer"), item("fixup")],
        )
        parent.workflow = workflow.compute(parent)
        self.assertEqual(parent.workflow.review_at, {3: 5, 4: 5})
        self.assertEqual(parent.review_at(3), 5)
        self.assertEqual(parent.covered_by(5), [3, 4])

    def test_the_plan_waits_fixture(self):
        """計画と待ち方の見本の表。実行ファイルの答えが表と同じ。"""
        with open(PLAN_WAITS, encoding="utf-8") as f:
            cases = json.load(f)["cases"]
        self.assertGreaterEqual(len(cases), 5)
        for case in cases:
            with self.subTest(case=case["name"]):
                fb = case.get("feedback")
                t = parsed(
                    "plan: " + json.dumps(case["plan"], ensure_ascii=False) + "\n",
                    ("feedback: " + json.dumps(fb, ensure_ascii=False) + "\n")
                    if fb is not None
                    else "",
                )
                wf = workflow.compute(t)
                self.assertEqual(
                    {str(n): w for n, w in wf.waits.items()}, case["waits"], case["name"]
                )
                self.assertEqual(
                    {str(n): at for n, at in wf.review_at.items()},
                    case["review_at"],
                    case["name"],
                )


class PlanProblemsTest(unittest.TestCase):
    def problems(self, plan, feedback=None, types=None):
        return [
            p.detail
            for p in workflow.problems(parent_of(plan, feedback), types)
            if p.severity == "error"
        ]

    def test_a_good_plan_passes(self):
        plan = [
            item("design"),
            item("acceptance", after=[1]),
            item("implement", after=[1]),
            item("docs", after=[2, 3]),
        ]
        self.assertEqual(self.problems(plan), [])

    def test_a_plan_without_after_has_more_than_one_end(self):
        found = self.problems([item("design"), item("docs")])
        self.assertTrue(any("最後の項" in d and "待たない" in d for d in found), found)

    def test_a_branch_cut_short_is_refused(self):
        found = self.problems(
            [
                item("design"),
                item("acceptance", after=[1]),
                item("implement", after=[1]),
                item("docs", after=[3]),
            ]
        )
        self.assertTrue(any("2" in d and "待たない" in d for d in found), found)

    def test_a_single_item_is_its_own_end(self):
        self.assertEqual(self.problems([item("design")]), [])

    def test_feedback_needs_one_end_too(self):
        plan = [item("design"), item("docs", after=[1])]
        self.assertEqual(self.problems(plan, []), [])
        self.assertEqual(self.problems(plan, [item("fixup")]), [])
        # いまはフィードバック計画を一直線で読むので、2 項でも最後の項が前を全部待つ。
        self.assertEqual(self.problems(plan, [item("fixup"), item("fixup")]), [])

    def test_the_last_item_of_either_plan_cannot_be_deferred(self):
        found = self.problems([item("design"), item("docs", "defer", [1])])
        self.assertTrue(any("最後の項は延期できない" in d for d in found), found)
        found = self.problems(
            [item("design"), item("docs", after=[1])], [item("fixup"), item("fixup", "defer")]
        )
        self.assertTrue(any("`feedback` の最後の項は延期できない" in d for d in found), found)

    def test_defer_without_a_taker_is_refused(self):
        found = self.problems(
            [
                item("design"),
                item("acceptance", "defer", [1]),
                item("implement", after=[1]),
                item("docs", after=[3, 1]),
            ]
        )
        self.assertTrue(any("引き受ける項が無い" in d for d in found), found)

    def test_the_taker_needs_a_review_when_the_definitions_are_given(self):
        types, _ = phasetypes.parse(DEFS)
        plan = [item("design", "defer"), item("extra", after=[1]), item("docs", after=[2])]
        found = self.problems(plan, types=types)
        self.assertTrue(any("レビューが無い" in d for d in found), found)
        # 定義を渡さなければ（判定の側）、定義を読む検査は当てない。
        self.assertFalse(any("レビューが無い" in d for d in self.problems(plan)))
        plan[1] = item("extra", "mr", [1])
        self.assertEqual(self.problems(plan, types=types), [])

    def test_lines_list_every_number_and_what_starts_at_once(self):
        parent = parent_of(
            [item("design"), item("extra"), item("docs", after=[1, 2])], [item("fixup")]
        )
        out = workflow.lines(parent, workflow.compute(parent))
        self.assertIn("1: design — 何も待たない", out)
        self.assertIn("3: docs — 待つ: 1, 2", out)
        self.assertIn("4: fixup — 待つ: 1, 2, 3", out)
        self.assertIn("すぐ始まる: 1, 2", out)
        broken = parent_of([item("design"), item("extra"), item("docs", after=[1])])
        self.assertIn("最後の項が待たない: 2", workflow.lines(broken, workflow.compute(broken)))


class AcceptedScopeTest(unittest.TestCase):
    def test_acceptance_reaches_only_the_phase_and_what_waits_on_it(self):
        import shutil
        import tempfile

        home = tempfile.mkdtemp(prefix="ccnavi-accepted-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        owner = parent_of(
            [
                item("design"),
                item("acceptance", after=[1]),
                item("implement", after=[1]),
                item("docs", after=[2, 3]),
            ]
        )
        owner.workflow = workflow.compute(owner)
        self.assertEqual(approval_marks.remember_accepted(home, "i0001", ["t-2"], 2), "")
        self.assertEqual(approval_marks.remember_accepted(home, "i0001", ["t-all"]), "")
        # 2（受入）で受け入れたものは、並行した 3（実装）では数えない。
        self.assertEqual(approval_marks.accepted_threads(home, "i0001", 3, owner), {"t-all"})
        # 2 と、2 を待つ 4（文書）では受け入れ済み。
        self.assertEqual(approval_marks.accepted_threads(home, "i0001", 2, owner), {"t-2", "t-all"})
        self.assertEqual(approval_marks.accepted_threads(home, "i0001", 4, owner), {"t-2", "t-all"})


class NextHintTest(unittest.TestCase):
    def test_an_earlier_branch_without_children_is_offered(self):
        """3 が先に閉じても、まだ子の無い 2 を次に始められるものとして挙げる。"""
        from ccnavi.tickets import phase as phase_mod

        owner = parent_of(
            [
                item("design"),
                item("acceptance", after=[1]),
                item("implement", after=[1]),
                item("docs", after=[2, 3]),
            ]
        )
        owner.workflow = workflow.compute(owner)
        done = ticket_model.Ticket(ticket="c", parent="i0001", review_required=False)
        phases = []
        for n in range(1, 5):
            ph = phase_mod.Phase("i0001", n, item=owner.plan[n - 1], owner=owner)
            if n in (1, 3):
                ph.tickets, ph.states = [done], {"c": ticket_model.DONE}
                ph.marks = {approval_marks.MARK_SKIPPED: {}}
            phases.append(ph)
        hint = phase_mod._next_hint(owner, phases, 3)
        self.assertIn("次に始められるのは 2", hint)


class LockTest(unittest.TestCase):
    """改版の錠は番号ごと。固定した番号（子が承認された番号）の項だけを変えさせない。"""

    def lock(self, current, revised, fixed):
        return [
            p.detail
            for p in agree_candidates.lock_problems(parent_of(revised), parent_of(current), fixed)
        ]

    # 承認済み: a(1)・b(2)・c(3)・d(4)。b と c は a を待ち、d は b と c を待つ。1 と 3 に子がある。
    CURRENT = [
        item("design"),
        item("acceptance", after=[1]),
        item("implement", after=[1]),
        item("docs", after=[2, 3]),
    ]

    def test_adding_an_item_before_the_end_passes(self):
        revised = [
            item("design"),
            item("acceptance", after=[1]),
            item("implement", after=[1]),
            item("extra", after=[1]),
            item("docs", after=[2, 3, 4]),
        ]
        self.assertEqual(self.lock(self.CURRENT, revised, {1, 3}), [])

    def test_an_unstarted_earlier_number_can_change(self):
        """前置の錠では落ちた形。2 番は子が無いので変えてよい。"""
        revised = [
            item("design"),
            item("extra", after=[1]),
            item("implement", after=[1]),
            item("docs", after=[2, 3]),
        ]
        self.assertEqual(self.lock(self.CURRENT, revised, {1, 3}), [])

    def test_a_fixed_number_keeps_its_definition_review_and_number(self):
        revised = [
            item("design"),
            item("acceptance", after=[1]),
            item("extra", after=[1]),
            item("docs", after=[2, 3]),
        ]
        found = self.lock(self.CURRENT, revised, {1, 3})
        self.assertTrue(any("3" in d and "子が承認されている" in d for d in found), found)
        found = self.lock(self.CURRENT, self.CURRENT[:2], {1, 3})
        self.assertTrue(found)

    def test_a_fixed_number_keeps_its_transitive_waits(self):
        revised = [
            item("design"),
            item("acceptance", after=[1]),
            item("implement", after=[2]),
            item("docs", after=[3]),
        ]
        found = self.lock(self.CURRENT, revised, {1, 3})
        self.assertTrue(any("3" in d and "待ち" in d for d in found), found)

    def test_a_fixed_number_waiting_on_an_unfixed_one_compares_the_item(self):
        """子を手で動かした親では、固定した 3 が子の無い 2 を待つことがある。"""
        current = [
            item("design"),
            item("acceptance", after=[1]),
            item("implement", after=[2]),
            item("docs", after=[3]),
        ]
        revised = [
            item("design"),
            item("extra", after=[1]),
            item("implement", after=[2]),
            item("docs", after=[3]),
        ]
        found = self.lock(current, revised, {1, 3})
        self.assertTrue(any("2" in d for d in found), found)

    def test_a_defer_taker_moves_only_between_unfixed_numbers(self):
        # a(1, 延期, 子あり)・x(2)・b(3, a と x を待つ) の引き受け手は 3。
        current = [item("design", "defer"), item("extra"), item("docs", after=[1, 2])]
        # 振り直して b が 2 番、x が 3 番。引き受け手は 2 番に変わるが、どちらも固定していない。
        revised = [item("design", "defer"), item("docs", after=[1]), item("extra", after=[2])]
        self.assertEqual(self.lock(current, revised, {1}), [])
        # 引き受け手が固定した番号なら、同じでなければ落とす。
        current = [item("design", "defer"), item("extra", after=[1]), item("docs", after=[2])]
        revised = [item("design", "defer"), item("extra"), item("docs", after=[1, 2])]
        found = self.lock(current, revised, {1, 2})
        self.assertTrue(any("延期の引き受け手" in d for d in found), found)

    def test_differs_compares_definition_review_and_transitive_waits(self):
        current = parent_of([item("a"), item("b", after=[1]), item("c", after=[2])])
        copied = parent_of([item("a"), item("b", after=[1]), item("c", after=[1, 2])])
        self.assertFalse(approval.plan_differs(copied, current))
        parallel = parent_of([item("a"), item("b", after=[1]), item("c", after=[1])])
        self.assertTrue(approval.plan_differs(parallel, current))
        current.feedback = []
        self.assertTrue(approval.plan_differs(copied, current))


class DagApprovalTest(PhaseHarness):
    def setUp(self):
        super().setUp()
        write(config_path(self.root, "phases"), DEFS)

    def copy(self, name="i0001"):
        path = os.path.join(self.approved, "doing", name + ".md")
        t, why = approval.load_copy(path, approved_dir=self.approved)
        self.assertIsNotNone(t, why)
        return t

    def scanned(self, name="i0001"):
        """判定が読むのと同じ走査（`approval.scan`）で読んだ承認済みチケット。"""
        conf, _ = settings.load(self.root)
        kept, _ = approval.scan(conf, self.root)
        return next(t for t in kept if t.ticket == name)

    def close_first_phase(self):
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/a/*"], review=False)
        )
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01-01", [("wip/a/x.md", "x\n")])
        self.assertEqual(self.close_child("i0001-01-01").returncode, 0)
        self.commit_parent("close 01")
        self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")

    def propose_branches(self):
        self.propose(
            "i0001-02-02", child_text("i0001-02-02", "i0001", 2, ["tests/a/*"], review=False)
        )
        self.propose(
            "i0001-03-03", child_text("i0001-03-03", "i0001", 3, ["src/a/*"], review=False)
        )
        self.commit_parent("propose 02 03")
        return self.approve()

    def approved_child(self, name):
        return os.path.exists(os.path.join(self.approved, "doing", name + ".md"))

    def move_by_hand(self, text, name="i0001"):
        """提案を通さずに承認済みの置き場へ置く（手で動かした承認）。"""
        write(os.path.join(self.approved, "doing", name + ".md"), text)
        self.commit_parent("moved by hand")

    def test_parallel_branches_start_together_and_join_waits(self):
        self.family(plan=PLAN)
        self.assertEqual(self.copy().workflow.waits, {1: [], 2: [1], 3: [1], 4: [1, 2, 3]})
        self.close_first_phase()
        result = self.propose_branches()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.approved_child("i0001-02-02"))
        self.assertTrue(self.approved_child("i0001-03-03"))
        # 合流点は、両方の枝が閉じるまで承認しない。
        self.propose("i0001-04-04", child_text("i0001-04-04", "i0001", 4, ["wip/d/*"]))
        self.commit_parent("propose 04")
        refused = self.approve()
        self.assertFalse(self.approved_child("i0001-04-04"))
        self.assertIn("閉じるまで承認しない", refused.stderr)

    def test_a_chain_still_waits_for_every_earlier_phase(self):
        self.family(plan=["design", "acceptance", "implement", "docs"])
        self.close_first_phase()
        result = self.propose_branches()
        self.assertTrue(self.approved_child("i0001-02-02"))
        self.assertFalse(self.approved_child("i0001-03-03"))
        self.assertIn("閉じるまで承認しない", result.stderr)

    def test_a_plan_without_after_is_not_approved(self):
        """`after` を 1 つも書かない 2 項以上の計画は、終端の検査で必ず落ちる。"""
        text = parent_text("i0001", ["design", {"type": "docs", "after": []}])
        self.propose("i0001", text)
        self.commit_parent()
        result = self.approve()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("待たない", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.approved, "doing", "i0001.md")))

    def test_the_approval_screen_lists_every_number(self):
        self.propose("i0001", parent_text("i0001", PLAN))
        self.commit_parent()
        result = self.ccnavi("--agree", stdin="n\n")
        self.assertIn("■ 待ち方", result.stdout)
        self.assertIn("1: design — 何も待たない", result.stdout)
        self.assertIn("3: implement — 待つ: 1", result.stdout)
        self.assertIn("4: docs — 待つ: 1, 2, 3", result.stdout)

    def test_no_workflow_file_is_written_and_the_ticket_is_moved_as_is(self):
        self.propose("i0001", parent_text("i0001", PLAN))
        self.commit_parent()
        proposal = os.path.join(self.parent_tree, "wip", "proposals", "todo", "i0001.md")
        with open(proposal, "rb") as f:
            before = f.read()
        self.assertEqual(self.approve().returncode, 0)
        with open(os.path.join(self.approved, "doing", "i0001.md"), "rb") as f:
            self.assertEqual(before, f.read())
        self.assertFalse(
            os.path.exists(os.path.join(self.approved, "phases", "i0001", "workflow.yml"))
        )
        self.assertEqual(self.scanned().workflow.waits[4], [1, 2, 3])

    def test_a_leftover_workflow_file_is_not_read_and_lint_says_so(self):
        self.family(plan=PLAN)
        held = os.path.join(self.approved, "phases", "i0001", "workflow.yml")
        write(held, "order: sequential\nwaits: {1: [], 2: [1], 3: [1, 2], 4: [1, 2, 3]}\n")
        self.commit_parent("leftover")
        self.assertEqual(self.scanned().workflow.waits[3], [1])
        linted = self.ccnavi("--lint")
        self.assertIn(".ccnavi/approved/phases/i0001/workflow.yml", linted.stdout)
        self.assertIn("読まない", linted.stdout)

    def test_a_workflow_written_in_a_proposal_is_refused(self):
        text = parent_text("i0001", PLAN).replace(
            "title: 親", "workflow: {order: dag, waits: {1: [], 2: [], 3: [], 4: []}}\ntitle: 親"
        )
        self.propose("i0001", text)
        self.commit_parent()
        result = self.approve()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("の欄は提案に書かない", result.stderr)

    def test_a_workflow_field_in_an_approved_copy_is_not_read_and_blocks(self):
        text = parent_text("i0001", PLAN).replace(
            "title: 親", "workflow: {order: dag, waits: {1: [], 2: [], 3: [], 4: []}}\ntitle: 親"
        )
        self.move_by_hand(text)
        held = self.scanned()
        self.assertEqual(held.workflow.waits[4], [1, 2, 3])
        self.assertIn("workflow", held.blocked)

    def test_changing_phases_yml_later_does_not_change_the_waits(self):
        self.family(plan=PLAN)
        write(
            config_path(self.root, "phases"), DEFS.replace("phases:", "order: sequential\nphases:")
        )
        self.close_first_phase()
        self.propose_branches()
        self.assertTrue(self.approved_child("i0001-03-03"))

    def test_a_hand_moved_parent_is_read_by_its_after(self):
        """手で動かした承認も、計画の `after` を信じて並行で読む。"""
        self.move_by_hand(parent_text("i0001", PLAN))
        self.assertEqual(self.scanned().blocked, "")
        self.close_first_phase()
        result = self.propose_branches()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.approved_child("i0001-03-03"))

    def test_a_hand_moved_broken_plan_stops_the_parent_and_its_children(self):
        broken = parent_text("i0001", ["design", {"type": "extra", "after": []}, "docs"])
        self.move_by_hand(broken)
        held = self.scanned()
        self.assertIn("待たない", held.blocked)
        # 親の範囲は当たらず、書き込みは止まる。
        denied = self.hook(
            "PreToolUse",
            "Write",
            self.parent_tree,
            file_path=os.path.join(self.parent_tree, "src", "x.py"),
            content="x",
        )
        self.assertIn("deny", denied.stdout)
        # 子の承認も止まる。
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/a/*"], review=False)
        )
        self.commit_parent()
        refused = self.approve()
        self.assertFalse(self.approved_child("i0001-01-01"))
        self.assertIn("計画が壊れている", refused.stderr)
        # 子を手で動かしても、判定と着手で止まる。
        self.move_by_hand(
            child_text("i0001-01-01", "i0001", 1, ["wip/a/*"], review=False), "i0001-01-01"
        )
        child = self.scanned("i0001-01-01")
        self.assertIn("計画が壊れている", child.blocked)
        self.start_parent_quietly()
        started = self.ccnavi("ticket", "start", "i0001-01-01")
        self.assertNotEqual(started.returncode, 0)

    def start_parent_quietly(self):
        self.ccnavi("ticket", "start", "i0001")

    def test_a_hand_moved_parent_with_a_broken_after_still_enters_the_index(self):
        """形の誤った `after` でも承認済みチケットは読め、判定は理由付きで止める。"""
        broken = parent_text("i0001", ["design", {"type": "docs", "after": [2]}])
        self.move_by_hand(broken)
        held = self.scanned()
        self.assertIn("after", held.blocked)
        linted = self.ccnavi("--lint")
        self.assertIn("after", linted.stdout)

    def test_a_revision_adding_an_item_passes_the_per_number_lock(self):
        self.family(plan=PLAN)
        self.close_first_phase()
        # 3（実装）だけに子を承認する。2（受入）は始めない。
        self.propose(
            "i0001-03-03", child_text("i0001-03-03", "i0001", 3, ["src/a/*"], review=False)
        )
        self.commit_parent("propose 03")
        self.assertEqual(self.approve().returncode, 0)
        self.assertTrue(self.approved_child("i0001-03-03"))
        # 子の無い 2 を別の定義に替え、文書の前に 1 項足す。
        revised = [
            "design",
            {"type": "extra", "after": [1]},
            {"type": "implement", "after": [1]},
            {"type": "acceptance", "after": [1]},
            {"type": "docs", "after": [2, 3, 4]},
        ]
        self.propose("i0001", parent_text("i0001", revised))
        self.commit_parent("revise")
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([i.type for i in self.copy().plan][1], "extra")
        self.assertEqual(self.copy().plan[4].after, [2, 3, 4])

    def test_a_revision_cannot_change_a_fixed_number(self):
        self.family(plan=PLAN)
        self.close_first_phase()
        revised = [
            "extra",
            {"type": "acceptance", "after": [1]},
            {"type": "implement", "after": [1]},
            {"type": "docs", "after": [2, 3]},
        ]
        self.propose("i0001", parent_text("i0001", revised))
        self.commit_parent("revise")
        result = self.approve()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("子が承認されている", result.stderr)

    def test_copying_the_waits_into_after_is_not_a_revision(self):
        self.family(plan=PLAN)
        copied = list(PLAN)
        copied[3] = {"type": "docs", "after": [1, 2, 3]}
        self.propose("i0001", parent_text("i0001", copied))
        self.commit_parent("copied")
        preview = self.ccnavi("--agree", "--preview", "--json")
        body = json.loads(preview.stdout)
        self.assertNotIn("i0001", [t.get("ticket") for t in body.get("tickets", [])])

    def test_a_plan_change_after_the_feedback_plan_is_refused(self):
        self.family(plan=["design"], feedback=[])
        revised = parent_text("i0001", ["design", "docs"], feedback=[])
        self.propose("i0001", revised)
        self.commit_parent("revise")
        result = self.approve()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("フィードバック計画", result.stderr)

    def test_explain_names_every_branch_at_work(self):
        self.family(plan=PLAN)
        self.close_first_phase()
        self.propose_branches()
        result = self.ccnavi("--explain", "--json")
        board = json.loads(result.stdout)
        owner = next(p for p in board["parents"] if p["ticket"] == "i0001")
        self.assertIn("2（受入）", owner["stage"])
        self.assertIn("3（実装）", owner["stage"])

    def test_the_content_check_does_not_trip_on_after(self):
        self.family(plan=PLAN)
        self.assertEqual([], approval_checks.content_problems(self.copy()))


if __name__ == "__main__":
    unittest.main()
