"""統合の子（maintenance: learning-consolidate）と、親の finish の学びの検査。

1. 親の finish は、処理されていない気づきがあると止まり、consolidate を案内する
2. consolidate は統合の子 `<親>-00-NN` を doing/ に作る（打ち直しで二重にできない）
3. 統合の子は記録もリスクもレビューも求めず done/ へ移る
4. 処理の行を書いてコミットすると親の finish が通る

統合の子の判定（子・phase 0・印の 3 つがそろったものだけ）と範囲（固定の定義を上限にし、親の範囲を
適用しない）は go の ticket・phase/scope・notice のテストが見る。
"""

from __future__ import annotations

import os
import unittest

from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import git, write
from tests.ticketsample import child_text, parent_text

EN = {"CCNAVI_LEARNING": "enable"}
PH = (
    "version: 1\nphases:\n  work:\n    kind: work\n    title: 作業\n"
    "    review: false\n    scope: inherit\n"
)
CHILD = "i0001-01-01"
CONS = "i0001-00-01"
RECORD = f"wip/handoff/learnings/{CHILD}.md"
ENTRY = "## 落とし穴\n\n- 種類: 未定\n- 内容: x\n"
RECORD_TEXT = f"---\nticket: {CHILD}\nparent: i0001\nlearnings: 1\n---\n\n{ENTRY}"


class FlowHarness(PhaseHarness):
    def setup_family(self, record=RECORD_TEXT, child_finish_ok=True):
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
        if not child_finish_ok:
            return closed
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
        # 書式の例（反映・対象外の両方）と、注釈なし・sources はチケット ID であることが出る
        self.assertIn("- 処理: 反映 <パターン名>", refused.stderr)
        self.assertIn("- 処理: 対象外 <理由>", refused.stderr)
        self.assertIn("注釈は付けない", refused.stderr)
        self.assertIn("チケット ID", refused.stderr)

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

    def test_broken_record_stops_the_child_finish(self):
        """子の finish も、親の finish と同じ検査で形式の誤りを断る。"""
        refused = self.setup_family(
            RECORD_TEXT.replace("learnings: 1", "learnings: 5"), child_finish_ok=False
        )
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn(RECORD, refused.stderr)
        self.assertIn("合わない", refused.stderr)

    def test_broken_record_stops_both_finish_and_consolidate(self):
        """合流のあとに記録が壊れたら、親の finish と consolidate が止まる。"""
        self.setup_family()
        write(
            os.path.join(self.parent_tree, *RECORD.split("/")),
            RECORD_TEXT.replace("learnings: 1", "learnings: 5"),
        )
        git(self.parent_tree, "add", "-f", RECORD)
        self.commit_parent("break")
        for result in (self.parent_finish(), self.consolidate()):
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(RECORD, result.stderr)
            self.assertIn("合わない", result.stderr)

    def test_consolidate_creates_one_open_child_and_is_idempotent(self):
        self.setup_family()
        made = self.consolidate()
        self.assertEqual(made.returncode, 0, made.stdout + made.stderr)
        self.assertIn(CONS, made.stdout)
        self.assertIn("- 処理: 反映 <パターン名>", made.stdout)
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


if __name__ == "__main__":
    unittest.main()
