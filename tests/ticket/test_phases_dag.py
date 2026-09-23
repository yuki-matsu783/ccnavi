"""全体計画を DAG で待たせる（設計 §9.7、ADR-0078）の受入テスト。

見るのは 6 つ。

1. 種類の `order` と `after` の読み方（循環、指す先、層の合わせ方）
2. `dag` では祖先でないフェーズを待たずに承認できる。一直線では待つ
3. 待ち方は承認のときに親へ写し、あとで phases.yml を直しても進行中の親には効かない
4. 計画が同じ改版で、直した phases.yml を進行中の親に効かせられる
5. 計画の検査（並び、終端、延期の引き受け手）
6. 受け入れはそのフェーズと、それを待つ番号にだけ効く
"""

from __future__ import annotations

import json
import os
import unittest

from ccnavi import approval, phasetypes, workflow
from ccnavi import ticket as ticket_mod
from tests import common_path
from tests.ticket.test_phases import PhaseHarness, child_text, parent_text
from tests.ticket.test_ticket import write

DAG = """
version: 1
order: dag
phases:
  design: {title: 設計, review: none, scope: ["wip/*"]}
  acceptance: {title: 受入, review: none, scope: ["tests/*"], after: [design]}
  implement: {title: 実装, review: none, scope: ["src/*"], after: [design]}
  docs: {title: 文書, review: mr, scope: inherit, after: [acceptance, implement]}
  fixup: {kind: feedback, title: 対応, review: mr, scope: inherit}
"""

SEQUENTIAL = DAG.replace("order: dag\n", "")

PLAN = ["design", "acceptance", "implement", "docs"]


def types_of(text: str, refs: bool = True) -> phasetypes.PhaseTypes:
    types, problems = phasetypes.parse(text, refs=refs)
    assert types is not None, problems
    return types


class TypesTest(unittest.TestCase):
    def test_order_defaults_to_sequential(self):
        self.assertEqual(types_of(SEQUENTIAL).order, phasetypes.ORDER_SEQUENTIAL)
        self.assertEqual(types_of(DAG).order, phasetypes.ORDER_DAG)

    def test_unknown_order_is_refused(self):
        types, problems = phasetypes.parse(DAG.replace("order: dag", "order: graph"))
        self.assertIsNone(types)
        self.assertIn("`order`", problems[0].detail)

    def test_after_cycle_is_refused(self):
        text = DAG.replace("design: {title: 設計,", "design: {title: 設計, after: [docs],")
        types, problems = phasetypes.parse(text)
        self.assertIsNone(types)
        self.assertTrue(any("循環" in p.detail for p in problems), problems)

    def test_after_may_point_only_at_work_types(self):
        text = DAG.replace("after: [acceptance, implement]", "after: [acceptance, fixup]")
        types, problems = phasetypes.parse(text)
        self.assertIsNone(types)
        self.assertTrue(any("指せるのは" in p.detail for p in problems), problems)

    def test_feedback_types_cannot_have_after(self):
        text = DAG.replace("fixup: {kind: feedback,", "fixup: {kind: feedback, after: [docs],")
        types, problems = phasetypes.parse(text)
        self.assertIsNone(types)
        self.assertTrue(any("`after` を持てる" in p.detail for p in problems), problems)

    def test_ancestors_follow_after_transitively(self):
        types = types_of(DAG)
        self.assertEqual(types.ancestors("docs"), {"design", "acceptance", "implement"})
        self.assertEqual(types.ancestors("design"), set())

    def test_dag_only_when_every_layer_says_so(self):
        """ファイルを持つ層が全部 `dag` と書いたときだけ `dag`。プロジェクト 1 本で緩めない。"""
        common = types_of(SEQUENTIAL)
        layer = types_of(
            "version: 1\norder: dag\nphases:\n  extra: {title: 追加, after: [design]}\n",
            refs=False,
        )
        merged, problems = phasetypes.merge(common, layer, "lib")
        self.assertEqual(merged.order, phasetypes.ORDER_SEQUENTIAL)
        self.assertTrue(any("食い違う" in p.detail for p in problems), problems)
        merged, _ = phasetypes.merge(types_of(DAG), layer, "lib")
        self.assertEqual(merged.order, phasetypes.ORDER_DAG)

    def test_after_counts_in_the_layer_comparison(self):
        """`after` だけが違う同じ id を「全欄同じ」として捨てない。"""
        common = types_of(DAG)
        layer = types_of("version: 1\norder: dag\nphases:\n  docs: {title: 文書, review: mr}\n")
        _, problems = phasetypes.merge(common, layer, "lib")
        self.assertTrue(any(p.severity == "error" for p in problems), problems)


class ComputeTest(unittest.TestCase):
    def parent(self, plan):
        return ticket_mod.Ticket(ticket="i0001", plan=[ticket_mod.PlanItem(type=t) for t in plan])

    def test_dag_waits_only_for_ancestors(self):
        wf = workflow.compute(self.parent(PLAN), types_of(DAG))
        self.assertEqual(wf.order, ticket_mod.WORKFLOW_DAG)
        self.assertEqual(wf.waits, {1: [], 2: [1], 3: [1], 4: [1, 2, 3]})

    def test_sequential_waits_for_everything_before(self):
        wf = workflow.compute(self.parent(PLAN), types_of(SEQUENTIAL))
        self.assertEqual(wf.waits, {1: [], 2: [1], 3: [1, 2], 4: [1, 2, 3]})

    def test_skipped_type_passes_its_wait_on(self):
        """計画に置かなかった種類は飛ばし、その種類が待っていたものを先へ引き継ぐ。"""
        wf = workflow.compute(self.parent(["design", "docs"]), types_of(DAG))
        self.assertEqual(wf.waits, {1: [], 2: [1]})

    def test_an_unreadable_type_makes_the_plan_sequential(self):
        wf = workflow.compute(self.parent(["design", "nope", "implement"]), types_of(DAG))
        self.assertEqual(wf.order, ticket_mod.WORKFLOW_SEQUENTIAL)
        self.assertEqual(wf.waits[3], [1, 2])

    def test_defer_goes_to_the_smallest_later_descendant(self):
        parent = self.parent(PLAN)
        parent.plan[1] = ticket_mod.PlanItem(type="acceptance", review="defer")
        wf = workflow.compute(parent, types_of(DAG))
        # 3（implement）は acceptance を待たないので引き受けない。4（docs）が引き受ける。
        self.assertEqual(wf.review_at, {2: 4})

    def test_feedback_plan_is_always_sequential(self):
        parent = self.parent(PLAN)
        parent.feedback = [ticket_mod.PlanItem(type="fixup"), ticket_mod.PlanItem(type="fixup")]
        parent.workflow = workflow.compute(parent, types_of(DAG))
        self.assertEqual(workflow.waits_of(parent, 6, types_of(DAG)), [1, 2, 3, 4, 5])


class PlanProblemsTest(unittest.TestCase):
    def problems(self, plan):
        parent = ticket_mod.Ticket(ticket="i0001", plan=[ticket_mod.PlanItem(type=t) for t in plan])
        return [p.detail for p in workflow.problems(parent, types_of(DAG))]

    def test_a_good_plan_passes(self):
        self.assertEqual(self.problems(PLAN), [])

    def test_reversed_order_is_refused(self):
        """逆に並べると依存が消えて並行に通るので、承認で止める。"""
        found = self.problems(["design", "docs", "acceptance", "implement", "docs"])
        self.assertTrue(any("先に要る" in d for d in found), found)

    def test_more_than_one_end_is_refused(self):
        found = self.problems(["design", "acceptance", "implement"])
        self.assertTrue(any("終端は 1 つ" in d for d in found), found)

    def test_the_same_end_type_twice_is_fine(self):
        self.assertEqual(self.problems(PLAN + ["docs"]), [])

    def test_defer_without_a_descendant_is_refused(self):
        parent = ticket_mod.Ticket(
            ticket="i0001",
            plan=[
                ticket_mod.PlanItem(type="design"),
                ticket_mod.PlanItem(type="acceptance", review="defer"),
                ticket_mod.PlanItem(type="implement"),
            ],
        )
        found = [p.detail for p in workflow.problems(parent, types_of(DAG))]
        self.assertTrue(any("引き受ける項が無い" in d for d in found), found)


class AcceptedScopeTest(unittest.TestCase):
    def test_acceptance_reaches_only_the_phase_and_what_waits_on_it(self):
        import shutil
        import tempfile

        home = tempfile.mkdtemp(prefix="ccnavi-accepted-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        owner = ticket_mod.Ticket(ticket="i0001", plan=[ticket_mod.PlanItem(type=t) for t in PLAN])
        owner.workflow = workflow.compute(owner, types_of(DAG))
        self.assertEqual(approval.remember_accepted(home, "i0001", ["t-2"], 2), "")
        self.assertEqual(approval.remember_accepted(home, "i0001", ["t-all"]), "")
        # 2（acceptance）で受け入れたものは、並行した 3（implement）では数えない。
        self.assertEqual(approval.accepted_threads(home, "i0001", 3, owner), {"t-all"})
        # 2 と、2 を待つ 4（docs）では受け入れ済み。
        self.assertEqual(approval.accepted_threads(home, "i0001", 2, owner), {"t-2", "t-all"})
        self.assertEqual(approval.accepted_threads(home, "i0001", 4, owner), {"t-2", "t-all"})
        # 番号を渡さなければ親全体。
        self.assertEqual(approval.accepted_threads(home, "i0001"), {"t-2", "t-all"})


class DagApprovalTest(PhaseHarness):
    def use(self, text):
        write(common_path(self.root, "phases"), text)

    def copy(self, name="i0001"):
        t, problems = ticket_mod.load(os.path.join(self.approved, "doing", name + ".md"))
        self.assertIsNotNone(t, problems)
        return t

    def close_first_phase(self):
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ["wip/a/*"], review=False))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01", [("wip/a/x.md", "x\n")])
        self.assertEqual(self.close_child("i0001-01").returncode, 0)
        self.commit_parent("close 01")
        self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")

    def propose_branches(self):
        self.propose("i0001-02", child_text("i0001-02", "i0001", 2, ["tests/a/*"], review=False))
        self.propose("i0001-03", child_text("i0001-03", "i0001", 3, ["src/a/*"], review=False))
        self.commit_parent("propose 02 03")
        return self.approve()

    def approved_child(self, name):
        return os.path.exists(os.path.join(self.approved, "doing", name + ".md"))

    def test_parallel_branches_start_together_and_join_waits(self):
        self.use(DAG)
        self.family(plan=PLAN)
        self.assertEqual(self.copy().workflow.waits, {1: [], 2: [1], 3: [1], 4: [1, 2, 3]})
        self.close_first_phase()
        result = self.propose_branches()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.approved_child("i0001-02"))
        self.assertTrue(self.approved_child("i0001-03"))
        # 合流点は、両方の枝が閉じるまで承認しない。
        self.propose("i0001-04", child_text("i0001-04", "i0001", 4, ["wip/d/*"]))
        self.commit_parent("propose 04")
        refused = self.approve()
        self.assertFalse(self.approved_child("i0001-04"))
        self.assertIn("閉じるまで承認しない", refused.stderr)

    def test_sequential_still_waits_for_every_earlier_phase(self):
        self.use(SEQUENTIAL)
        self.family(plan=PLAN)
        self.close_first_phase()
        result = self.propose_branches()
        self.assertTrue(self.approved_child("i0001-02"))
        self.assertFalse(self.approved_child("i0001-03"))
        self.assertIn("閉じるまで承認しない", result.stderr)

    def test_changing_phases_yml_later_does_not_loosen_a_running_parent(self):
        """一直線で承認した親は、あとで `dag` に書き換えても並行にならない。"""
        self.use(SEQUENTIAL)
        self.family(plan=PLAN)
        self.use(DAG)
        self.close_first_phase()
        self.propose_branches()
        self.assertTrue(self.approved_child("i0001-02"))
        self.assertFalse(self.approved_child("i0001-03"))

    def test_changing_phases_yml_later_does_not_tighten_a_running_parent(self):
        self.use(DAG)
        self.family(plan=PLAN)
        self.use(SEQUENTIAL)
        self.close_first_phase()
        self.propose_branches()
        self.assertTrue(self.approved_child("i0001-03"))

    def test_a_revision_with_the_same_plan_applies_the_new_phases_yml(self):
        self.use(SEQUENTIAL)
        self.family(plan=PLAN)
        self.assertEqual(self.copy().workflow.order, ticket_mod.WORKFLOW_SEQUENTIAL)
        self.use(DAG)
        self.propose("i0001", parent_text("i0001", PLAN))
        self.commit_parent("revise")
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("待ち方の変更", result.stdout)
        self.assertEqual(self.copy().workflow.order, ticket_mod.WORKFLOW_DAG)

    def test_the_approval_screen_shows_what_runs_in_parallel(self):
        self.use(DAG)
        self.propose("i0001", parent_text("i0001", PLAN))
        self.commit_parent()
        result = self.approve()
        self.assertIn("■ 待ち方", result.stdout)
        self.assertIn("3: implement — 待つ: 1", result.stdout)

    def test_a_workflow_written_in_a_proposal_is_not_taken(self):
        """待ち方の写しを書くのは `--approve` だけ。提案に書いてあっても使わない。"""
        self.use(SEQUENTIAL)
        text = parent_text("i0001", PLAN).replace(
            "title: 親", "workflow: {order: dag, waits: {1: [], 2: [], 3: [], 4: []}}\ntitle: 親"
        )
        self.propose("i0001", text)
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        wf = self.copy().workflow
        self.assertEqual(wf.order, ticket_mod.WORKFLOW_SEQUENTIAL)
        self.assertEqual(wf.waits[4], [1, 2, 3])

    def test_a_plan_that_breaks_the_dag_is_not_approved(self):
        self.use(DAG)
        self.propose("i0001", parent_text("i0001", ["design", "acceptance", "implement"]))
        self.commit_parent()
        result = self.approve()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("終端は 1 つ", result.stderr)

    def test_explain_names_every_branch_at_work(self):
        self.use(DAG)
        self.family(plan=PLAN)
        self.close_first_phase()
        self.propose_branches()
        result = self.ccnavi("--explain", "--json")
        board = json.loads(result.stdout)
        owner = next(p for p in board["parents"] if p["ticket"] == "i0001")
        self.assertIn("2（受入）", owner["stage"])
        self.assertIn("3（実装）", owner["stage"])


if __name__ == "__main__":
    unittest.main()
