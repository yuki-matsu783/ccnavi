"""全体計画の順序を計画のステップの `after` で決めるの受入テスト。

見るのは 5 つ。

1. 承認と順序。並行の枝が一緒に始まり、合流点には両方が先行する
2. 先行関係のファイルは無い。承認済みチケットの計画から都度計算し、残ったファイルは読まない
3. 手で動かした承認も `after` で読む。壊れた計画の親は判定で止まり、子も止まる
4. 改版と番号ごとの錠
5. ボードは並行の枝を挙げる

フェーズ定義の読み方・`after` の読み方・先行関係の計算（見本の表 tests/fixtures/plan-waits.json）・
計画の検査・改版の見つけ方と錠・次の案内は go/internal/tickets の config/phases・model/ticket・
proposal/candidates・phase が見る。
"""

from __future__ import annotations

import json
import os
import re
import unittest

import yaml

from tests import config_path
from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import write
from tests.ticketsample import child_text
from tests.ticketsample import parent_text as _parent_text

# 順序の欄を持たない定義。順序は計画のステップの `after` が決める。
DEFS = """
version: 1
phases:
  design: {title: 設計, review: false, scope: ["wip/*"]}
  acceptance: {title: 受入, review: false, scope: ["tests/*"]}
  implement: {title: 実装, review: false, scope: ["src/*"]}
  docs: {title: 文書, review: true, scope: inherit}
  extra: {title: 追加, review: false, scope: ["wip/*"]}
  fixup: {kind: feedback, title: 対応, review: true, scope: inherit}
"""


def parent_text(*args, **kwargs):
    """親の提案の本文。`phases:` はこのテストの定義（DEFS）からコピーする。"""
    kwargs.setdefault("copy", DEFS)
    return _parent_text(*args, **kwargs)


# 設計 → 受入と実装が並行 → 文書が両方を受ける（終端）。
PLAN = [
    "design",
    {"type": "acceptance", "after": [1]},
    {"type": "implement", "after": [1]},
    {"type": "docs", "after": [2, 3]},
]


class DagApprovalTest(PhaseHarness):
    phases_text = DEFS

    def setUp(self):
        super().setUp()
        write(config_path(self.root, "phases"), DEFS)

    def front(self, name="i0001"):
        """承認済みチケットの frontmatter。"""
        with open(os.path.join(self.approved, "doing", name + ".md"), encoding="utf-8") as f:
            return yaml.safe_load(f.read().split("---\n", 2)[1])

    def waits(self, name="i0001"):
        """`--explain` が言う、フェーズごとの先行（番号 → 先行の番号の並び）。"""
        out = self.ccnavi("--explain").stdout
        found = {}
        for number, rest in re.findall(rf"^  {name} フェーズ (\d+)（[^）]*）: (.*)$", out, re.M):
            tail = rest.rsplit(" / ", 1)[-1]
            found[int(number)] = (
                [] if tail == "先行フェーズなし" else [int(n) for n in tail[3:].split(", ")]
            )
        return found

    def lint_says(self, name):
        """`--lint` の、その識別子を名指す error の文。"""
        problems = json.loads(self.ccnavi("--lint", "--json").stdout)["problems"]
        return "\n".join(
            p["detail"]
            for p in problems
            if p["severity"] == "error" and (name in p["detail"] or name in p["where"])
        )

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
        self.commit_parent("approval-request 02 03")
        return self.approve()

    def approved_child(self, name):
        return os.path.exists(os.path.join(self.approved, "doing", name + ".md"))

    def move_by_hand(self, text, name="i0001"):
        """提案を通さずに承認済みの置き場へ置く（手で動かした承認）。"""
        write(os.path.join(self.approved, "doing", name + ".md"), text)
        self.commit_parent("moved by hand")

    def test_parallel_branches_start_together_and_join_waits(self):
        self.family(plan=PLAN)
        self.assertEqual(self.waits(), {1: [], 2: [1], 3: [1], 4: [1, 2, 3]})
        self.close_first_phase()
        result = self.propose_branches()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.approved_child("i0001-02-02"))
        self.assertTrue(self.approved_child("i0001-03-03"))
        # 合流点は、両方の枝が閉じるまで承認しない。
        self.propose("i0001-04-04", child_text("i0001-04-04", "i0001", 4, ["wip/d/*"]))
        self.commit_parent("approval-request 04")
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
        """`after` を 1 つも書かない 2 ステップ以上の計画は、終端の検査で必ず落ちる。"""
        text = parent_text("i0001", ["design", {"type": "docs", "after": []}])
        self.propose("i0001", text)
        self.commit_parent()
        result = self.approve()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("先行していない", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.approved, "doing", "i0001.md")))

    def test_the_approval_screen_lists_every_number(self):
        self.propose("i0001", parent_text("i0001", PLAN))
        self.commit_parent()
        result = self.ccnavi("user", "agree", stdin="n\n")
        self.assertIn("■ フェーズ間の依存関係", result.stdout)
        self.assertIn("1: design — 先行なし", result.stdout)
        self.assertIn("3: implement — 先行 1", result.stdout)
        self.assertIn("4: docs — 先行 1, 2, 3", result.stdout)

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
        self.assertEqual(self.waits()[4], [1, 2, 3])

    def test_a_leftover_workflow_file_is_not_read(self):
        self.family(plan=PLAN)
        held = os.path.join(self.approved, "phases", "i0001", "workflow.yml")
        write(held, "order: sequential\nwaits: {1: [], 2: [1], 3: [1, 2], 4: [1, 2, 3]}\n")
        self.commit_parent("leftover")
        self.assertEqual(self.waits()[3], [1])

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
        self.assertEqual(self.waits()[4], [1, 2, 3])
        self.assertIn("workflow", self.lint_says("i0001"))

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
        self.assertEqual("", self.lint_says("i0001"))
        self.close_first_phase()
        result = self.propose_branches()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.approved_child("i0001-03-03"))

    def test_a_hand_moved_broken_plan_stops_the_parent_and_its_children(self):
        broken = parent_text("i0001", ["design", {"type": "extra", "after": []}, "docs"])
        self.move_by_hand(broken)
        self.assertIn("先行していない", self.lint_says("i0001"))
        # 親の範囲はヒットせず、書き込みは止まる。
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
        self.assertIn("計画が不正", refused.stderr)
        # 子を手で動かしても、判定と着手で止まる。
        self.move_by_hand(
            child_text("i0001-01-01", "i0001", 1, ["wip/a/*"], review=False), "i0001-01-01"
        )
        self.assertIn("計画が不正", self.lint_says("i0001-01-01"))
        self.start_parent_quietly()
        started = self.ccnavi("ticket", "start", "i0001-01-01")
        self.assertNotEqual(started.returncode, 0)

    def start_parent_quietly(self):
        self.ccnavi("ticket", "start", "i0001")

    def test_a_hand_moved_parent_with_a_broken_after_still_enters_the_index(self):
        """形の誤った `after` でも承認済みチケットは読め、判定は理由付きで止める。"""
        broken = parent_text("i0001", ["design", {"type": "docs", "after": [2]}])
        self.move_by_hand(broken)
        self.assertIn("after", self.lint_says("i0001"))
        linted = self.ccnavi("--lint")
        self.assertIn("after", linted.stdout)

    def test_a_revision_adding_an_item_passes_the_per_number_lock(self):
        self.family(plan=PLAN)
        self.close_first_phase()
        # 3（実装）だけに子を承認する。2（受入）は始めない。
        self.propose(
            "i0001-03-03", child_text("i0001-03-03", "i0001", 3, ["src/a/*"], review=False)
        )
        self.commit_parent("approval-request 03")
        self.assertEqual(self.approve().returncode, 0)
        self.assertTrue(self.approved_child("i0001-03-03"))
        # 子の無い 2 を別の定義に替え、文書の前に 1 ステップ足す。
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
        plan = self.front()["plan"]
        self.assertEqual(plan[1]["type"], "extra")
        self.assertEqual(plan[4]["after"], [2, 3, 4])

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
        self.assertEqual("", self.lint_says("i0001"))


if __name__ == "__main__":
    unittest.main()
