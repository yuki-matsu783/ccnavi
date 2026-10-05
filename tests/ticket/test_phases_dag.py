"""全体計画を DAG で待たせる（設計 9.7）の受入テスト。

見るのは 6 つ。

1. 種類の `order` と `after` の読み方（循環、指す先、レイヤーの合わせ方）
2. `dag` では祖先でないフェーズを待たずに承認できる。一直線では待つ
3. 待ち方は承認のときに親へコピーし、あとで phases.yml を直しても進行中の親には反映されない
4. 計画が同じ改版で、直した phases.yml を進行中の親に反映できる
5. 計画の検査（順序、終端、延期の引き受け手）
6. 受け入れはそのフェーズと、それを待つ番号にだけ当てはまる
"""

from __future__ import annotations

import json
import os
import unittest

from ccnavi.infra import settings
from ccnavi.tickets import approval, approval_checks, approval_marks, phasetypes, workflow
from ccnavi.tickets import ticket as ticket_mod
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

    def test_after_and_overlap_on_the_same_pair_is_refused(self):
        """両方あると待ち方は overlap を採り、書いた依存が消える。どちらかにさせる。"""
        text = DAG.replace(
            'implement: {title: 実装, review: none, scope: ["src/*"], after: [design]}',
            'implement: {title: 実装, review: none, scope: ["src/*"], after: [design], '
            "overlap: [design]}",
        )
        types, problems = phasetypes.parse(text)
        self.assertIsNone(types)
        self.assertTrue(any("after と overlap の両方" in p.detail for p in problems), problems)

    def test_ancestors_follow_after_transitively(self):
        types = types_of(DAG)
        self.assertEqual(types.ancestors("docs"), {"design", "acceptance", "implement"})
        self.assertEqual(types.ancestors("design"), set())

    def test_dag_only_when_every_layer_says_so(self):
        """ファイルを持つレイヤーが全部 `dag` と書いたときだけ `dag`。
        プロジェクト 1 本で緩めない。"""
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

    def test_defer_goes_only_to_a_phase_that_waits(self):
        """overlap で待ちから外れた番号は、延期を引き受けない。"""
        text = DAG.replace(
            "docs: {title: 文書, review: mr, scope: inherit, after: [acceptance, implement]}",
            "docs: {title: 文書, review: mr, scope: inherit, after: [implement]}\n"
            "  wrap: {title: 締め, review: mr, scope: inherit, after: [docs, acceptance]}",
        ).replace("acceptance: {title: 受入,", "acceptance: {title: 受入, overlap: [docs],")
        parent = self.parent(["design", "acceptance", "implement", "docs", "wrap"])
        parent.plan[1] = ticket_mod.PlanItem(type="acceptance", review="defer")
        wf = workflow.compute(parent, types_of(text))
        self.assertNotIn(2, wf.waits[4])
        self.assertEqual(wf.review_at, {2: 5})

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
        self.assertEqual(approval_marks.remember_accepted(home, "i0001", ["t-2"], 2), "")
        self.assertEqual(approval_marks.remember_accepted(home, "i0001", ["t-all"]), "")
        # 2（acceptance）で受け入れたものは、並行した 3（implement）では数えない。
        self.assertEqual(approval_marks.accepted_threads(home, "i0001", 3, owner), {"t-all"})
        # 2 と、2 を待つ 4（docs）では受け入れ済み。
        self.assertEqual(approval_marks.accepted_threads(home, "i0001", 2, owner), {"t-2", "t-all"})
        self.assertEqual(approval_marks.accepted_threads(home, "i0001", 4, owner), {"t-2", "t-all"})
        # 番号を渡さなければ親全体。
        self.assertEqual(approval_marks.accepted_threads(home, "i0001"), {"t-2", "t-all"})
        # 並行した 3 で同じスレッドを受け入れ直せば、3 でも有効
        self.assertEqual(approval_marks.remember_accepted(home, "i0001", ["t-2"], 3), "")
        self.assertEqual(approval_marks.accepted_threads(home, "i0001", 3, owner), {"t-2", "t-all"})
        # 親全体で受け入れたものは、番号付きで受け入れ直しても狭まらない
        self.assertEqual(approval_marks.remember_accepted(home, "i0001", ["t-all"], 2), "")
        self.assertEqual(approval_marks.accepted_threads(home, "i0001", 3, owner), {"t-2", "t-all"})


class NextHintTest(unittest.TestCase):
    def test_an_earlier_branch_without_children_is_offered(self):
        """3 が先に閉じても、まだ子の無い 2 を次に始められるものとして挙げる。"""
        from ccnavi.tickets import phase as phase_mod

        owner = ticket_mod.Ticket(ticket="i0001", plan=[ticket_mod.PlanItem(type=t) for t in PLAN])
        owner.workflow = workflow.compute(owner, types_of(DAG))
        done = ticket_mod.Ticket(ticket="c", parent="i0001", review_required=False)
        phases = []
        for n in range(1, 5):
            ph = phase_mod.Phase("i0001", n, item=owner.plan[n - 1], owner=owner)
            if n in (1, 3):
                ph.tickets, ph.states = [done], {"c": ticket_mod.DONE}
                ph.marks = {approval_marks.MARK_SKIPPED: {}}
            phases.append(ph)
        hint = phase_mod._next_hint(owner, phases, 3)
        self.assertIn("次に始められるのは 2", hint)


class DagApprovalTest(PhaseHarness):
    def use(self, text):
        write(common_path(self.root, "phases"), text)

    def copy(self, name="i0001"):
        # 待ち方は承認済みチケットの外（`phases/<親>/workflow.yml`）に在るので、置き場を渡して読む。
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

    def test_parallel_branches_start_together_and_join_waits(self):
        self.use(DAG)
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

    def test_sequential_still_waits_for_every_earlier_phase(self):
        self.use(SEQUENTIAL)
        self.family(plan=PLAN)
        self.close_first_phase()
        result = self.propose_branches()
        self.assertTrue(self.approved_child("i0001-02-02"))
        self.assertFalse(self.approved_child("i0001-03-03"))
        self.assertIn("閉じるまで承認しない", result.stderr)

    def test_changing_phases_yml_later_does_not_loosen_a_running_parent(self):
        """一直線で承認した親は、あとで `dag` に書き換えても並行にならない。"""
        self.use(SEQUENTIAL)
        self.family(plan=PLAN)
        self.use(DAG)
        self.close_first_phase()
        self.propose_branches()
        self.assertTrue(self.approved_child("i0001-02-02"))
        self.assertFalse(self.approved_child("i0001-03-03"))

    def test_changing_phases_yml_later_does_not_tighten_a_running_parent(self):
        self.use(DAG)
        self.family(plan=PLAN)
        self.use(SEQUENTIAL)
        self.close_first_phase()
        self.propose_branches()
        self.assertTrue(self.approved_child("i0001-03-03"))

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

    def test_a_workflow_written_in_a_proposal_is_refused(self):
        """待ち方のコピーを書くのは `--agree` だけ。提案に書いてあれば承認しない。

        引用符付きの鍵でも同じ。
        """
        self.use(SEQUENTIAL)
        for key in ("workflow", '"workflow"'):
            text = parent_text("i0001", PLAN).replace(
                "title: 親",
                f"{key}: {{order: dag, waits: {{1: [], 2: [], 3: [], 4: []}}}}\ntitle: 親",
            )
            self.propose("i0001", text)
            self.commit_parent()
            result = self.approve()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("の欄は提案に書かない", result.stderr)
            self.assertFalse(os.path.exists(os.path.join(self.approved, "doing", "i0001.md")))

    def test_the_workflow_is_fixed_in_its_own_file_and_not_in_the_ticket(self):
        """承認は待ち方を `phases/<親>/workflow.yml` に固定し、承認済みチケットには書き足さない。"""
        self.use(DAG)
        self.propose("i0001", parent_text("i0001", PLAN))
        self.commit_parent()
        proposal = os.path.join(self.parent_tree, "wip", "proposals", "todo", "i0001.md")
        with open(proposal, "rb") as f:
            before = f.read()
        self.assertEqual(self.approve().returncode, 0)
        with open(os.path.join(self.approved, "doing", "i0001.md"), "rb") as f:
            self.assertEqual(before, f.read())
        held = approval.workflow_path(self.approved, "i0001")
        self.assertTrue(os.path.isfile(held))
        self.assertEqual(self.copy().workflow.order, ticket_mod.WORKFLOW_DAG)

    def test_an_approved_parent_without_a_workflow_is_read_as_sequential(self):
        """コピーした待ち方を持たない承認済みの親は、
        いまの phases.yml から計算せず一直線で待たせる。"""
        self.use(SEQUENTIAL)
        self.family(plan=PLAN)
        os.remove(approval.workflow_path(self.approved, "i0001"))
        self.commit_parent("drop workflow")
        self.assertIsNone(self.copy().workflow)
        self.use(DAG)
        self.close_first_phase()
        self.propose_branches()
        self.assertTrue(self.approved_child("i0001-02-02"))
        self.assertFalse(self.approved_child("i0001-03-03"))

    def _rewrite_copy(self, extra: str) -> None:
        """承認済みの親の frontmatter の頭に欄を足し、待ち方のファイルを消す。"""
        os.remove(approval.workflow_path(self.approved, "i0001"))
        path = os.path.join(self.approved, "doing", "i0001.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        write(path, text.replace("---\n", "---\n" + extra, 1))

    def test_an_old_copy_with_the_record_still_reads_its_workflow_field(self):
        """前の版の承認（記録 `ccnavi_approved` と `workflow:` を書き足した形）は、
        欄の待ち方が今の phases.yml から計算した待ち方と同じなら、欄の待ち方で読む。"""
        self.use(DAG)
        self.family(plan=PLAN)
        self._rewrite_copy(
            "ccnavi_approved: {approved_at: '2026-01-01T00:00:00+09:00', source_tree: main, "
            "source_path: wip/proposals/todo/i0001.md}\n"
            "workflow: {order: dag, waits: {1: [], 2: [1], 3: [1], 4: [1, 2, 3]}, review_at: {}}\n"
        )
        held = self.copy()
        self.assertEqual(held.workflow.order, ticket_mod.WORKFLOW_DAG)
        self.assertEqual(held.approved_at, "2026-01-01T00:00:00+09:00")
        self.assertEqual([], approval_checks.content_problems(held))
        self.commit_parent("old form")
        held = self.scanned()
        self.assertEqual(held.workflow.order, ticket_mod.WORKFLOW_DAG)
        self.assertFalse(held.workflow_record_differs)

    def test_a_forged_old_workflow_that_differs_from_the_computed_one_is_read_as_sequential(self):
        """記録を揃えて書いた古い形でも、欄の待ち方が今の phases.yml から計算した待ち方と
        違えば欄を使わず一直線で読み、`--lint` と status が warn で言う。"""
        self.use(SEQUENTIAL)
        self.family(plan=PLAN)
        self._rewrite_copy(
            "ccnavi_approved: {approved_at: '2026-01-01T00:00:00+09:00', source_tree: main, "
            "source_path: wip/proposals/todo/i0001.md}\n"
            "workflow: {order: dag, waits: {1: [], 2: [], 3: [], 4: []}, review_at: {}}\n"
        )
        self.commit_parent("forge")
        held = self.scanned()
        self.assertTrue(approval_checks.has_record(held))
        self.assertIsNone(held.workflow)
        self.assertTrue(held.workflow_record_differs)
        self.assertEqual(
            workflow.effective(held, None).as_raw(), workflow.compute(held, None).as_raw()
        )
        self.assertEqual(workflow.waits_of(held, 4, None), [1, 2, 3])
        linted = self.ccnavi("--lint")
        self.assertIn("今の phases.yml から計算した待ち方と違う", linted.stdout + linted.stderr)
        status = self.ccnavi("ticket", "status", "i0001")
        self.assertIn("今の phases.yml から計算した待ち方と違う", status.stdout)

    def test_a_workflow_field_in_a_new_copy_is_not_read_and_blocks(self):
        """記録を持たない承認済みチケットの `workflow:` 欄は、承認済みの待ち方として
        効かせず、止める。"""
        self.use(SEQUENTIAL)
        self.family(plan=PLAN)
        self._rewrite_copy("workflow: {order: dag, waits: {1: [], 2: [], 3: [], 4: []}}\n")
        held = self.copy()
        self.assertIsNone(held.workflow)
        found = [p.detail for p in approval_checks.content_problems(held)]
        self.assertTrue(any("workflow" in d for d in found), found)

    def test_a_half_written_record_does_not_pass_for_the_old_form(self):
        """書きかけの記録（`{}`・`approved_at` が空・欄が足りない）は古い形とみなさず、
        `workflow:` 欄で止める。"""
        self.use(SEQUENTIAL)
        self.family(plan=PLAN)
        path = os.path.join(self.approved, "doing", "i0001.md")
        held_path = approval.workflow_path(self.approved, "i0001")
        with open(path, "rb") as f:
            original = f.read()
        with open(held_path, "rb") as f:
            held_bytes = f.read()
        for record in (
            "ccnavi_approved: {}\n",
            "ccnavi_approved: {approved_at: '', source_tree: main, source_path: p}\n",
            "ccnavi_approved: {approved_at: '2026-01-01T00:00:00+09:00'}\n",
        ):
            with open(path, "wb") as f:
                f.write(original)
            with open(held_path, "wb") as f:
                f.write(held_bytes)
            self._rewrite_copy(
                record + "workflow: {order: dag, waits: {1: [], 2: [], 3: [], 4: []}}\n"
            )
            held = self.copy()
            self.assertFalse(approval_checks.has_record(held), record)
            self.assertIsNone(held.workflow, record)
            found = [p.detail for p in approval_checks.content_problems(held)]
            self.assertTrue(any("workflow" in d for d in found), (record, found))

    def test_an_unreadable_workflow_file_blocks(self):
        self.use(DAG)
        self.family(plan=PLAN)
        write(approval.workflow_path(self.approved, "i0001"), "order: nope\n")
        held = self.copy()
        self.assertIsNone(held.workflow)
        self.assertIn("待ち方のファイル", held.workflow_unreadable)
        self.assertTrue(approval_checks.content_problems(held))

    def test_a_deeply_nested_workflow_file_is_unreadable_and_blocks(self):
        """入れ子が深すぎる待ち方のファイルで例外が漏れず、読めないものとして止める。"""
        self.use(DAG)
        self.family(plan=PLAN)
        write(approval.workflow_path(self.approved, "i0001"), "[" * 5000 + "\n")
        wf, why = approval.read_workflow(self.approved, "i0001")
        self.assertIsNone(wf)
        self.assertIn("YAML として読めない", why)
        held = self.copy()
        self.assertIsNone(held.workflow)
        self.assertIn("待ち方のファイル", held.workflow_unreadable)
        self.assertTrue(approval_checks.content_problems(held))

    def test_a_workflow_file_left_without_its_parent_is_warned_by_lint(self):
        """親の承認済みチケットがどこにも無いのに残った待ち方のファイルを、
        `--lint` が warn で言う。"""
        self.use(DAG)
        self.family(plan=PLAN)
        said = "待ち方のファイルがあるのに、親 i0001 の承認済みチケットがどの置き場"
        linted = self.ccnavi("--lint")
        self.assertNotIn(said, linted.stdout + linted.stderr)
        os.remove(os.path.join(self.approved, "doing", "i0001.md"))
        self.commit_parent("drop the parent")
        linted = self.ccnavi("--lint")
        self.assertIn(".ccnavi/approved/phases/i0001/workflow.yml: " + said, linted.stdout)

    def test_a_revision_cannot_move_a_defer_target_behind_approved_children(self):
        def reviewed(text):
            return text.replace(
                'review: none, scope: ["tests', 'review: mr, scope: ["tests'
            ).replace('review: none, scope: ["src', 'review: mr, scope: ["src')

        self.use(reviewed(DAG))
        plan = ["design", ("acceptance", "defer"), "implement", "docs"]
        self.propose("i0001", parent_text("i0001", plan))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.assertEqual(self.copy().workflow.review_at, {2: 4})
        self.close_first_phase()
        self.propose_branches()
        # 一直線にすると、2 の延期は 3（もう子がある）が引き受けることになる
        self.use(reviewed(SEQUENTIAL))
        self.propose("i0001", parent_text("i0001", plan))
        self.commit_parent("revise")
        result = self.approve()
        self.assertIn("延期の引き受け手が変わる", result.stderr)
        self.assertEqual(self.copy().workflow.order, ticket_mod.WORKFLOW_DAG)

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
