"""統合の子（maintenance: learning-consolidate）と、親の finish の学びの検査。

1. 親の finish は、処理されていない気づきがあると止まり、consolidate を案内する
2. consolidate は統合の子 `<親>-00-NN` を doing/ に作る（打ち直しで二重にできない）
3. 統合の子は記録もリスクもレビューも求めず done/ へ移る
4. 処理の行を書いてコミットすると親の finish が通る
5. 統合の子の判定は子・phase 0・印の 3 つがそろったものだけで、組み合わせの誤りは構造エラー
6. 範囲は固定の定義を上限にし、親の範囲を適用しない
"""

from __future__ import annotations

import os
import unittest

from ccnavi.infra import settings
from ccnavi.policy import rules
from ccnavi.tickets.approval import checks as approval_checks
from ccnavi.tickets.config import phasetypes
from ccnavi.tickets.model import ticket
from ccnavi.tickets.phase import scope as phase_scope
from ccnavi.tickets.proposal import agree_candidates
from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import git, write
from tests.ticketsample import child_text, parent_text

EN = {"CCNAVI_LEARNING": "enable"}
PH = (
    "version: 1\nphases:\n  work:\n    kind: work\n    title: 作業\n"
    "    review: none\n    scope: inherit\n"
)
CHILD = "i0001-01-01"
CONS = "i0001-00-01"
RECORD = f"wip/handoff/learnings/{CHILD}.md"
ENTRY = "## 落とし穴\n\n- 種類: 未定\n- 内容: x\n"
RECORD_TEXT = f"---\nticket: {CHILD}\nparent: i0001\nlearnings: 1\n---\n\n{ENTRY}"


def cons_text(**front):
    base = {
        "version": "1",
        "ticket": CONS,
        "parent": "i0001",
        "phase": "0",
        "maintenance": "learning-consolidate",
    }
    base.update(front)
    lines = ["---"] + [f"{k}: {v}" for k, v in base.items() if v is not None]
    lines += [
        "human_review:",
        "  required: false",
        "  reason: t",
        "title: 統合",
        "rationale: r",
        "allow:",
        "  - match: Write|Edit",
        '    glob: "docs/wiki/patterns/*"',
        "  - match: Write|Edit",
        '    glob: "src/*"',
        'started_at: ""',
        "---",
        "",
        "本文",
    ]
    return "\n".join(lines) + "\n"


class FlowHarness(PhaseHarness):
    def setup_family(self, record=RECORD_TEXT):
        write(self.phases, PH)
        scope = ("src/*", "wip/*", "docs/*")
        self.propose("i0001", parent_text("i0001", ["work"], feedback=[], allow=scope, copy=PH))
        self.propose(CHILD, child_text(CHILD, "i0001", 1, list(scope), review=False))
        self.commit_parent()
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        self.start_parent()
        tree = self.worktree(CHILD, "i0001")
        started = self.ccnavi("ticket", "start", CHILD, env=EN)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        write(os.path.join(tree, *RECORD.split("/")), record)
        git(tree, "add", "-f", "-A")
        git(tree, "commit", "--quiet", "-m", "rec")
        closed = self.ccnavi("ticket", "finish", CHILD, env=EN)
        self.assertEqual(closed.returncode, 0, closed.stdout + closed.stderr)
        self.merge(CHILD)
        return tree

    def parent_finish(self):
        return self.ccnavi("ticket", "finish", "i0001", env=EN)

    def consolidate(self, env=EN):
        return self.ccnavi("ticket", "consolidate", "i0001", env=env)

    def doing(self, ident):
        return os.path.join(self.approved, "doing", ident + ".md")

    def cons_files(self):
        folder = os.path.join(self.approved, "doing")
        return sorted(n for n in os.listdir(folder) if "-00-" in n) if os.path.isdir(folder) else []


class ConsolidateFlowTest(FlowHarness):
    def test_parent_finish_stops_on_unprocessed_entry(self):
        self.setup_family()
        refused = self.parent_finish()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("落とし穴", refused.stderr)
        self.assertIn(RECORD, refused.stderr)
        self.assertIn("consolidate", refused.stderr)

    def test_all_none_passes_without_consolidation(self):
        self.setup_family(RECORD_TEXT.replace("learnings: 1", "learnings: none").replace(ENTRY, ""))
        done = self.parent_finish()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_parent_finish_stops_on_uncommitted_learning_files(self):
        self.setup_family(RECORD_TEXT.replace("learnings: 1", "learnings: none").replace(ENTRY, ""))
        write(os.path.join(self.parent_tree, "docs", "wiki", "patterns", "p.md"), "x\n")
        refused = self.parent_finish()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("docs/wiki/patterns/p.md", refused.stderr)
        self.assertIn("コミット", refused.stderr)

    def test_inbox_file_stops_the_parent(self):
        self.setup_family(RECORD_TEXT.replace("learnings: 1", "learnings: none").replace(ENTRY, ""))
        write(
            os.path.join(self.parent_tree, "docs", "wiki", "inbox", "feat-1.md"),
            "---\nlearnings: 1\n---\n\n## a\n",
        )
        self.commit_parent("inbox")
        refused = self.parent_finish()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("docs/wiki/inbox/feat-1.md", refused.stderr)

    def test_broken_record_stops_both_finish_and_consolidate(self):
        self.setup_family(RECORD_TEXT.replace("learnings: 1", "learnings: 5"))
        for result in (self.parent_finish(), self.consolidate()):
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(RECORD, result.stderr)
            self.assertIn("合わない", result.stderr)

    def test_consolidate_creates_one_open_child_and_is_idempotent(self):
        self.setup_family()
        made = self.consolidate()
        self.assertEqual(made.returncode, 0, made.stdout + made.stderr)
        self.assertIn(CONS, made.stdout)
        self.assertEqual(self.cons_files(), [CONS + ".md"])
        with open(self.doing(CONS), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("maintenance: learning-consolidate", text)
        self.assertIn("phase: 0", text)
        again = self.consolidate()
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn(CONS, again.stdout)
        self.assertEqual(self.cons_files(), [CONS + ".md"])

    def test_consolidate_is_refused_when_nothing_to_process(self):
        self.setup_family(RECORD_TEXT.replace("learnings: 1", "learnings: none").replace(ENTRY, ""))
        refused = self.consolidate()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("処理すべき", refused.stderr)
        self.assertEqual(self.cons_files(), [])

    def test_consolidate_does_nothing_when_disabled(self):
        self.setup_family()
        refused = self.consolidate(env={"CCNAVI_LEARNING": "disable"})
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("disable", refused.stderr)
        self.assertEqual(self.cons_files(), [])

    def test_consolidate_requires_other_finish_checks(self):
        self.setup_family()
        self.propose("i0001-01-02", child_text("i0001-01-02", "i0001", 1, ["src/*"], review=False))
        self.commit_parent("open child")
        self.approve()
        refused = self.consolidate()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("開いている子", refused.stderr)

    def test_full_cycle(self):
        self.setup_family()
        self.assertEqual(self.consolidate().returncode, 0)
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "consolidate child")
        tree = self.worktree(CONS, "i0001")
        started = self.ccnavi("ticket", "start", CONS, env=EN)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        # 学びの案内は出ない
        self.assertNotIn("学びの蓄積", started.stdout)
        # 統合の子が処理の行を書く
        path = os.path.join(tree, *RECORD.split("/"))
        with open(path, encoding="utf-8") as f:
            text = f.read()
        write(path, text.rstrip("\n") + "\n- 処理: 対象外 環境が原因\n")
        git(tree, "add", "-f", "-A")
        git(tree, "commit", "--quiet", "-m", "process")
        # 記録を求めず、リスクも測らず、レビューなしで done/ へ移る
        closed = self.ccnavi("ticket", "finish", CONS, env=EN)
        self.assertEqual(closed.returncode, 0, closed.stdout + closed.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.approved, "done", CONS + ".md")))
        self.assertFalse(os.path.exists(self.doing(CONS)))
        self.assertNotIn("リスク", closed.stdout)
        self.merge(CONS)
        done = self.parent_finish()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_second_consolidation_advances_the_sequence(self):
        self.setup_family()
        self.assertEqual(self.consolidate().returncode, 0)
        os.makedirs(os.path.join(self.approved, "done"), exist_ok=True)
        os.replace(self.doing(CONS), os.path.join(self.approved, "done", CONS + ".md"))
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "closed")
        again = self.consolidate()
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("i0001-00-02", again.stdout)


class SessionRejectsTest(FlowHarness):
    def test_the_fixed_scope_is_not_reported_as_outside_the_parent(self):
        from ccnavi.tickets.notice import session_rejects

        self.setup_family()
        self.assertEqual(self.consolidate().returncode, 0)
        conf, _ = settings.load(self.root)
        found = session_rejects.find_all(conf, self.root)
        self.assertEqual([r for r in found.rejects if r.ticket == CONS and "親" in r.origin], [])


class StructureTest(unittest.TestCase):
    def parse(self, text):
        found, problems = ticket.parse(text)
        self.assertIsNotNone(found, problems)
        return found

    def test_three_conditions_make_a_consolidate_child(self):
        t = self.parse(cons_text())
        self.assertTrue(t.is_consolidate)
        self.assertEqual(t.maintenance_problem(), "")

    def test_structure_errors(self):
        # 印はあるが phase が 0 でない（識別子の番号も揃える）
        t = self.parse(cons_text(ticket="i0001-01-01", phase="1"))
        self.assertFalse(t.is_consolidate)
        self.assertIn("phase", t.maintenance_problem())
        # 知らない値
        t = self.parse(cons_text(maintenance="other"))
        self.assertFalse(t.is_consolidate)
        self.assertIn("知らない値", t.maintenance_problem())
        # 親に印
        parent = parent_text("i0001", ["work"], feedback=[], copy=PH).replace(
            "plan:", "maintenance: learning-consolidate\nplan:", 1
        )
        t = self.parse(parent)
        self.assertIn("親に", t.maintenance_problem())

    def test_phase_zero_without_mark_is_not_exempt(self):
        t = self.parse(cons_text(maintenance=None))
        self.assertFalse(t.is_consolidate)
        self.assertEqual(t.maintenance_problem(), "")
        parent = self.parse(parent_text("i0001", ["work"], feedback=[], copy=PH))
        problems = approval_checks.child_problems(t, parent)
        self.assertTrue(problems)
        self.assertIn("計画にありません", problems[0].detail)

    def test_consolidate_child_is_not_checked_against_the_plan(self):
        t = self.parse(cons_text())
        parent = self.parse(parent_text("i0001", ["work"], feedback=[], copy=PH))
        self.assertEqual(approval_checks.child_problems(t, parent), [])

    def test_structure_error_blocks_in_both_sides(self):
        parent = self.parse(parent_text("i0001", ["work"], feedback=[], copy=PH))
        bad = self.parse(cons_text(maintenance="other"))
        self.assertTrue(approval_checks.child_problems(bad, parent))
        problems, _ = agree_candidates.validate(bad, {"i0001": parent}, approved=True)
        self.assertTrue([p for p in problems if p.severity == rules.SEVERITY_ERROR])
        pool = {"i0001": parent}
        pool[bad.ticket] = bad
        self.assertTrue(approval_checks.blocking_problems(_conf(), bad, pool))

    def test_proposals_with_the_mark_are_not_approvable(self):
        parent = self.parse(parent_text("i0001", ["work"], feedback=[], copy=PH))
        t = self.parse(cons_text())
        problems, _ = agree_candidates.validate(t, {"i0001": parent})
        self.assertTrue(problems)
        self.assertIn("通常の提案・承認の経路では指定できない", problems[0].detail)


def _conf():
    from ccnavi.infra import settings

    return settings.Settings()


class ScopeTest(unittest.TestCase):
    def setUp(self):
        self.child, _ = ticket.parse(cons_text())
        self.parent, _ = ticket.parse(
            parent_text("i0001", ["work"], feedback=[], allow=("src/*",), copy=PH)
        )

    def verdict(self, rel):
        pt = phase_scope.type_for(self.child, self.parent)
        return phase_scope.scope_verdict(self.child, self.parent, pt, rel)

    def test_fixed_definition_is_the_upper_limit(self):
        self.assertEqual(phasetypes.consolidate_type().scope_globs[0], "docs/wiki/patterns/*")
        self.assertFalse(self.verdict("docs/wiki/patterns/a.md").outside)
        # 子の allow に src/* があっても、固定の定義の外は止まる
        self.assertTrue(self.verdict("src/x.py").outside)
        # 子の allow に無い場所は、固定の定義の中でも止まる（受信箱は allow にない）
        self.assertTrue(self.verdict("docs/wiki/inbox/a.md").outside)

    def test_ordinary_child_is_still_cut_by_parent(self):
        child, _ = ticket.parse(child_text("i0001-01-01", "i0001", 1, ["src/*", "docs/*"]))
        pt = phase_scope.type_for(child, self.parent)
        self.assertTrue(phase_scope.scope_verdict(child, self.parent, pt, "docs/a.md").outside)


if __name__ == "__main__":
    unittest.main()
