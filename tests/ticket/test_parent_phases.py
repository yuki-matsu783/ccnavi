"""親に固定するフェーズ定義（親の `phases:`、設計 9.7）の受入テスト。

計画を持つ親は、計画が使う定義だけの写しを `phases:` に持つ。見るのは 6 つ。

1. 承認（`--agree`）の検査。写しは承認したときの `phases.yml` の同じ名前の定義と読んだ形で
   同じでなければ落ちる。使う定義は写しに書く。`phases.yml` にあって写しに無い定義は何も
   言わない。写しにあって使われない定義は warn。承認画面は定義を 3 つに分けて出す
2. 承認のあとの判定は写しを読み、`phases.yml` を読まない（直しても進行中の親は変わらない）
3. 写しの無い計画付きの親と、写しを書いた子は、手で動かしても判定で止まる
4. 改版の規則。写しだけの違いも改版になり、子が承認された番号の定義は変えられない
5. 補助のフラグ `--plan-order <親> --fill-phases`
6. 定義の `scope` と `deliverables` は 20 件まで
"""

from __future__ import annotations

import json
import os
import unittest

import yaml

from ccnavi.tickets import agree_digest, phase_forms, phasetypes
from ccnavi.tickets import ticket as ticket_mod
from tests import phasecopy
from tests.ticket.test_phases import (
    PHASES,
    PHASES_WITHOUT_RESEARCH,
    PhaseHarness,
    child_text,
    parent_text,
)
from tests.ticket.test_ticket import git, write


def front_of(text: str) -> dict:
    """本文の frontmatter を読む。"""
    head = text.split("---\n", 2)[1]
    return yaml.safe_load(head)


def with_phases(text: str, phases: dict) -> str:
    """`phases:` の無い親の本文の `plan:` の前に、渡した定義の `phases:` を差し込む。"""
    block = yaml.safe_dump(
        {"phases": phases}, allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    return text.replace("plan:", block + "plan:", 1)


RESEARCH = phasecopy.definitions(PHASES)["research"]
DESIGN = phasecopy.definitions(PHASES)["design"]


class CopyAgreeTest(PhaseHarness):
    """1. 承認の検査と承認画面。"""

    def propose_parent(self, text):
        self.propose("i0001", text)
        self.commit_parent()
        return self.approve()

    def test_a_matching_copy_is_approved_as_is_and_the_screen_sorts_the_definitions(self):
        text = parent_text("i0001", ["research", "design"])
        result = self.propose_parent(text)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # 承認は中身を変えない。写しは承認の前に提案へ書いてある。
        with open(os.path.join(self.approved, "doing", "i0001.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), text)
        screen = result.stdout
        self.assertIn("■ この親に固定するフェーズ定義", screen)
        fixed = screen.split("■ この親に固定するフェーズ定義", 1)[1].split("■", 1)[0]
        self.assertIn("調査 / research", fixed)
        self.assertIn("wip/research/*", fixed)
        self.assertIn("wip/research/summary.md", fixed)
        self.assertIn("使う番号: 1", fixed)
        self.assertIn("使う番号: 2", fixed)
        # phases.yml にあってこの親では使わない定義は、名前と題を 1 行に並べる。止めない。
        self.assertIn("■ この親では使わない定義", screen)
        unused = screen.split("■ この親では使わない定義", 1)[1].split("■", 1)[0]
        self.assertIn("受入テスト作成", unused)
        self.assertNotIn("調査", unused)
        self.assertNotIn("使われていない定義", screen)

    def test_a_copy_whose_values_differ_from_phases_yml_is_refused(self):
        text = with_phases(
            parent_text("i0001", ["research", "design"], copy=None),
            {"research": RESEARCH, "design": {**DESIGN, "review": "none"}},
        )
        result = self.propose_parent(text)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("phases.yml の定義と違う", result.stderr)
        self.assertIn("review", result.stderr)
        self.assertIn("--plan-order i0001 --fill-phases", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.approved, "doing", "i0001.md")))

    def test_writing_style_does_not_count_as_a_difference(self):
        """読んだ形で比べる。キーの順・書き方・既定値を省いた書き方の違いは同じとみなす。"""
        research = dict(reversed(list(RESEARCH.items())))
        research["scope"] = ["wip/research/*/"]
        design = {k: v for k, v in DESIGN.items() if k != "review"}  # 既定は mr
        text = with_phases(
            parent_text("i0001", ["research", "design"], copy=None),
            {"research": research, "design": design},
        )
        result = self.propose_parent(text)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_copy_name_missing_from_phases_yml_is_refused(self):
        text = with_phases(
            parent_text("i0001", ["research", "extra"], copy=None),
            {"research": RESEARCH, "extra": {"kind": "work", "title": "余分", "review": "none"}},
        )
        result = self.propose_parent(text)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("`extra` は phases.yml に無い", result.stderr)

    def test_a_plan_item_whose_definition_is_not_in_the_copy_is_refused(self):
        text = with_phases(
            parent_text("i0001", ["research", "design"], copy=None), {"research": RESEARCH}
        )
        result = self.propose_parent(text)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("`design` が `phases:` に無い", result.stderr)

    def test_a_parent_with_a_plan_and_no_copy_is_refused(self):
        result = self.propose_parent(parent_text("i0001", ["research", "design"], copy=None))
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("`phases:` が無い", result.stderr)
        self.assertIn("--fill-phases", result.stderr)

    def test_an_unused_definition_is_a_warning_and_shown_on_the_screen(self):
        acceptance = phasecopy.definitions(PHASES)["acceptance"]
        text = with_phases(
            parent_text("i0001", ["research", "design"], copy=None),
            {"research": RESEARCH, "design": DESIGN, "acceptance": acceptance},
        )
        result = self.propose_parent(text)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("■ 使われていない定義", result.stdout)
        unused = result.stdout.split("■ 使われていない定義", 1)[1].split("■", 1)[0]
        self.assertIn("受入テスト作成 / acceptance", unused)

    def test_a_child_with_phases_is_refused(self):
        self.family(plan=("research", "design"))
        text = child_text("i0001-01-01", "i0001", 1, ["wip/research/*"]).replace(
            "phase: 1\n", "phase: 1\nphases: {}\n", 1
        )
        self.propose("i0001-01-01", text)
        self.commit_parent()
        result = self.approve()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("親だけの欄", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.approved, "doing", "i0001-01-01.md")))


class CopyJudgeTest(PhaseHarness):
    """2・3. 承認のあとの判定は写しを読む。"""

    def write_to(self, tree, rel):
        return self.hook(
            "PreToolUse",
            "Write",
            tree,
            file_path=os.path.join(tree, *rel.split("/")),
            content="x\n",
        )

    def decision(self, result):
        if not result.stdout.strip():
            return ""
        return json.loads(result.stdout).get("hookSpecificOutput", {}).get("permissionDecision", "")

    def approved_child(self, review=True):
        self.family(plan=("research", "design"))
        self.propose(
            "i0001-01-01",
            child_text("i0001-01-01", "i0001", 1, ["wip/research/*", "src/a/*"], review=review),
        )
        self.commit_parent("propose child")
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        return self.run_child("i0001-01-01")

    def test_editing_phases_yml_after_approval_does_not_change_a_running_parent(self):
        tree = self.approved_child()
        for label, text in (
            ("定義を広げた", PHASES.replace('scope: ["wip/research/*"]', 'scope: ["src/*"]')),
            ("定義を消した", PHASES_WITHOUT_RESEARCH),
            ("phases.yml が壊れた", "version: 1\nphases: [\n"),
        ):
            with self.subTest(label):
                write(self.phases, text)
                # 写した定義の上限（wip/research/*）で切り詰めたまま。
                beyond = self.write_to(tree, "src/a/x.py")
                self.assertEqual(self.decision(beyond), "deny", beyond.stdout)
                self.assertIn("limit: phase type 調査 (research)", self.reason(beyond))
                inside = self.write_to(tree, "wip/research/note.md")
                self.assertNotEqual(self.decision(inside), "deny", self.reason(inside))

    def test_phases_and_their_titles_and_reviews_come_from_the_copy_after_approval(self):
        """承認のあとに phases.yml の定義を直しても、フェーズの題・見る場所・止め方と
        SubagentStart の案内は写しのまま。"""
        tree = self.approved_child(review=False)
        write(
            self.phases,
            PHASES.replace(
                "    title: 調査\n    review: none\n", "    title: 調べ\n    review: mr\n"
            ).replace("agent: explorer", "agent: planner"),
        )
        board = json.loads(self.ccnavi("--explain", "--json").stdout)
        owner = next(p for p in board["parents"] if p["ticket"] == "i0001")
        shown = [(p["number"], p["review_kind"], p["title"]) for p in owner["phases"]]
        self.assertEqual(shown, [(1, "none", "調査"), (2, "mr", "設計")])
        said = self.reason(self.hook("SubagentStart", "", tree, agent_id="sub-1"))
        self.assertIn("1: 調査", said)
        self.assertIn("explorer", said)
        self.assertNotIn("調べ", said)
        self.assertNotIn("planner", said)
        # 写しの research はレビュー不要。閉じても止めない（phases.yml の mr なら止まる）。
        write(os.path.join(tree, "wip", "research", "summary.md"), "まとめ\n")
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", "summary")
        self.assertEqual(self.close_child("i0001-01-01").returncode, 0)
        self.commit_parent("close 01")
        gate, _, _ = self.board_phase(1)
        self.assertFalse(gate)

    def test_a_child_is_approved_from_the_copy_after_phases_yml_lost_its_definition(self):
        self.family(plan=("research", "design"))
        write(self.phases, PHASES_WITHOUT_RESEARCH)
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/research/*"]))
        self.commit_parent()
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.approved, "doing", "i0001-01-01.md")))

    def place_by_hand(self, name, text):
        """提案を通さずに承認済みの置き場へ置く（手で動かした承認）。"""
        write(os.path.join(self.approved, "doing", name + ".md"), text)
        self.commit_parent("by hand")

    def test_a_parent_moved_by_hand_without_a_copy_is_blocked_and_so_is_its_child(self):
        self.place_by_hand("i0001", parent_text("i0001", ["research", "design"], copy=None))
        self.place_by_hand("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/research/*"]))
        tree = self.worktree("i0001-01-01", "i0001")
        result = self.write_to(tree, "wip/research/note.md")
        self.assertEqual(self.decision(result), "deny", result.stdout)
        self.assertIn("DENY_TICKET_BLOCKED", self.reason(result))
        self.assertIn("phases:", self.reason(result))
        lint = self.ccnavi("--lint", "--mode", "enable")
        self.assertNotEqual(lint.returncode, 0)
        self.assertIn("`phases:` が無い", lint.stdout)

    def test_a_child_moved_by_hand_with_phases_is_blocked(self):
        self.family(plan=("research", "design"))
        text = child_text("i0001-01-01", "i0001", 1, ["wip/research/*"]).replace(
            "phase: 1\n", "phase: 1\nphases: {}\n", 1
        )
        self.place_by_hand("i0001-01-01", text)
        tree = self.worktree("i0001-01-01", "i0001")
        result = self.write_to(tree, "wip/research/note.md")
        self.assertEqual(self.decision(result), "deny", result.stdout)
        self.assertIn("DENY_TICKET_BLOCKED", self.reason(result))
        self.assertIn("親だけの欄", self.reason(result))

    def test_a_copy_moved_by_hand_is_trusted_and_lint_says_it_drifted(self):
        """手で動かした承認では、写しが `phases.yml` と違ってもそのまま効く。`--lint` が warn。"""
        narrow = {**RESEARCH, "scope": ["wip/research/a/*"]}
        self.place_by_hand(
            "i0001",
            with_phases(
                parent_text("i0001", ["research", "design"], copy=None),
                {"research": narrow, "design": DESIGN},
            ),
        )
        self.place_by_hand("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/research/*"]))
        tree = self.worktree("i0001-01-01", "i0001")
        inside = self.write_to(tree, "wip/research/a/x.md")
        self.assertNotEqual(self.decision(inside), "deny", self.reason(inside))
        beyond = self.write_to(tree, "wip/research/b/x.md")
        self.assertEqual(self.decision(beyond), "deny", beyond.stdout)
        self.assertIn("limit: phase type", self.reason(beyond))
        lint = self.ccnavi("--lint", "--mode", "enable")
        self.assertIn("今の phases.yml と違う", lint.stdout)
        self.assertIn("進行中の親には効かない", lint.stdout)

    def test_lint_says_a_lost_phases_yml_once_as_a_warning_for_an_approved_parent(self):
        """承認済みの親は写しを読むので、phases.yml が無くなっても --lint は warn だけで言う。"""
        self.family(plan=("research", "design"))
        os.remove(self.phases)
        lint = self.ccnavi("--lint", "--mode", "enable", "--json")
        found = [p for p in json.loads(lint.stdout)["problems"] if "i0001" in p.get("detail", "")]
        self.assertTrue(found, lint.stdout)
        self.assertEqual({p["severity"] for p in found}, {"warn"}, found)

    def test_deliverables_come_from_the_copy(self):
        self.family(plan=("research", "design"))
        self.propose(
            "i0001-01-01",
            child_text("i0001-01-01", "i0001", 1, ["wip/research/*"], review=False),
        )
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        # 承認のあとに phases.yml の成果物を変えても、閉じるときに見るのは写しの成果物。
        write(
            self.phases,
            PHASES.replace('["wip/research/summary.md"]', '["wip/research/other.md"]'),
        )
        self.run_child("i0001-01-01", [("wip/research/summary.md", "まとめ\n")])
        closed = self.close_child("i0001-01-01")
        self.assertEqual(closed.returncode, 0, closed.stdout + closed.stderr)


class CopyRevisionTest(PhaseHarness):
    """4. 改版の規則。"""

    def test_a_proposal_that_only_changes_the_copy_is_a_revision(self):
        self.family(plan=("research", "design"))
        changed = PHASES.replace("title: 設計\n", "title: 設計と方針\n")
        write(self.phases, changed)
        self.propose("i0001", parent_text("i0001", ["research", "design"], copy=changed))
        self.commit_parent()
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("親の改版", result.stdout)
        self.assertIn("■ phases: の差分", result.stdout)
        diff = result.stdout.split("■ phases: の差分", 1)[1].split("■", 1)[0]
        self.assertIn("design", diff)
        self.assertIn("title", diff)
        with open(os.path.join(self.approved, "doing", "i0001.md"), encoding="utf-8") as f:
            front = front_of(f.read())
        self.assertEqual(front["phases"]["design"]["title"], "設計と方針")

    def approve_first_child(self):
        self.family(plan=("research", "design"))
        self.propose(
            "i0001-01-01",
            child_text("i0001-01-01", "i0001", 1, ["wip/research/*"], review=False),
        )
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)

    def test_a_fixed_number_keeps_its_definition_even_when_phases_yml_changed(self):
        self.approve_first_child()
        changed = PHASES.replace('scope: ["wip/research/*"]', 'scope: ["wip/research/*", "x/*"]')
        write(self.phases, changed)
        plan = ["research", "design", "acceptance"]
        # 子が承認された 1 番（research）の定義は承認済みチケットの値のまま、ほかは今の phases.yml。
        kept = with_phases(
            parent_text("i0001", plan, copy=None),
            {
                "research": RESEARCH,
                "design": DESIGN,
                "acceptance": phasecopy.definitions(changed)["acceptance"],
            },
        )
        self.propose("i0001", kept)
        self.commit_parent()
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_fixed_number_cannot_take_the_new_definition(self):
        self.approve_first_child()
        changed = PHASES.replace('scope: ["wip/research/*"]', 'scope: ["wip/research/*", "x/*"]')
        write(self.phases, changed)
        self.propose(
            "i0001", parent_text("i0001", ["research", "design", "acceptance"], copy=changed)
        )
        self.commit_parent()
        result = self.approve()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("子が承認された番号が使う定義", result.stderr)

    def test_revised_front_replaces_the_copy_with_the_proposal(self):
        """改版の書き込みは計画と一緒に `phases:` も提案の値に差し替える。"""
        current, _ = ticket_mod.parse(parent_text("i0001", ["design"]))
        revised, _ = ticket_mod.parse(
            parent_text("i0001", ["design"], feedback=["implement-feedback"])
        )
        front = agree_digest.revised_front(current, revised)
        self.assertEqual(set(front["phases"]), {"design", "implement-feedback"})
        self.assertEqual(front["phases"], revised.raw["phases"])


class FillPhasesTest(PhaseHarness):
    """5. `--plan-order <親> --fill-phases`。"""

    def path(self):
        return os.path.join(self.parent_tree, "wip", "proposals", "todo", "i0001.md")

    def read(self):
        with open(self.path(), encoding="utf-8") as f:
            return f.read()

    def fill(self, name="i0001"):
        return self.ccnavi("--plan-order", name, "--fill-phases")

    def test_fill_phases_writes_only_the_used_definitions_and_keeps_other_bytes(self):
        original = parent_text("i0001", ["research", "design"], copy=None).replace(
            "title: 親\n", "# 題の前のコメント\ntitle: 親\n", 1
        )
        self.propose("i0001", original)
        result = self.fill()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        text = self.read()
        front = front_of(text)
        self.assertEqual(list(front["phases"]), ["research", "design"])
        self.assertIn("# 題の前のコメント", text)
        without = {k: v for k, v in front.items() if k != "phases"}
        self.assertEqual(without, front_of(original))
        self.assertEqual(text.split("---\n", 2)[2], original.split("---\n", 2)[2])
        self.commit_parent()
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)

    def test_fill_phases_replaces_a_stale_copy_and_drops_unused_definitions(self):
        acceptance = phasecopy.definitions(PHASES)["acceptance"]
        self.propose(
            "i0001",
            with_phases(
                parent_text("i0001", ["research", "design"], copy=None),
                {"design": {**DESIGN, "review": "none"}, "acceptance": acceptance},
            ),
        )
        result = self.fill()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        front = front_of(self.read())
        self.assertEqual(set(front["phases"]), {"research", "design"})
        self.assertEqual(front["phases"]["design"]["review"], "mr")
        # 2 度打っても変わらない。
        before = self.read()
        again = self.fill()
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(self.read(), before)

    def test_fill_phases_on_a_revision_keeps_the_definitions_of_fixed_numbers(self):
        self.family(plan=("research", "design"))
        self.propose(
            "i0001-01-01",
            child_text("i0001-01-01", "i0001", 1, ["wip/research/*"], review=False),
        )
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        changed = PHASES.replace('scope: ["wip/research/*"]', 'scope: ["x/*"]').replace(
            "title: 設計\n", "title: 設計と方針\n"
        )
        write(self.phases, changed)
        self.propose("i0001", parent_text("i0001", ["research", "design", "acceptance"], copy=None))
        result = self.fill()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        front = front_of(self.read())
        self.assertEqual(front["phases"]["research"]["scope"], ["wip/research/*"])
        self.assertEqual(front["phases"]["design"]["title"], "設計と方針")
        self.commit_parent()
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)

    def test_fill_phases_writes_nothing_when_other_fields_would_change(self):
        """`phases:` の値にアンカーがあり、ほかの欄が別名で指していれば書かない。"""
        text = parent_text("i0001", ["research"], copy=None).replace(
            "plan:", "phases: &copied {}\nrationale_extra: *copied\nplan:", 1
        )
        self.propose("i0001", text)
        result = self.fill()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("書かない", result.stderr)
        self.assertEqual(self.read(), text)

    def fill_in(self, cwd, name="i0001"):
        """`--fill-phases` を cwd を変えて打つ（エージェントが自分のワークツリーから打つ形）。"""
        from tests.inproc import run_ccnavi

        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment.pop("CLAUDE_PROJECT_DIR", None)
        return run_ccnavi(
            [
                "--root",
                self.root,
                "--approved",
                ".ccnavi/approved",
                "--state",
                self.state,
                "--log",
                "",
                "--plan-order",
                name,
                "--fill-phases",
            ],
            cwd=cwd,
            env=environment,
        )

    def test_fill_phases_writes_only_the_copy_in_the_callers_tree(self):
        """同じ提案が 2 つのツリーにあれば、打ったツリーの側だけを書く。

        どちらのツリーでもないところから打てば、何も書かない。"""
        text = parent_text("i0001", ["research"], copy=None)
        self.propose("i0001", text)
        other = self.worktree("elsewhere", "main")
        there = os.path.join(other, "wip", "proposals", "todo", "i0001.md")
        write(there, text)
        refused = self.fill()
        self.assertNotEqual(refused.returncode, 0, refused.stdout + refused.stderr)
        self.assertIn("複数の場所", refused.stderr)
        self.assertEqual(self.read(), text)
        done = self.fill_in(self.parent_tree)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("phases:", self.read())
        with open(there, encoding="utf-8") as f:
            self.assertEqual(f.read(), text)

    def test_fill_phases_names_a_column_zero_comment_inside_the_value(self):
        """`phases:` の値の中に行頭のコメントがあれば、値の範囲が決まらないので書かずにそう言う。"""
        text = parent_text("i0001", ["research"], copy=None).replace(
            "plan:", "phases:\n  research:\n# 行頭のコメント\n    kind: work\nplan:", 1
        )
        self.propose("i0001", text)
        result = self.fill()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("行頭", result.stderr)
        self.assertEqual(self.read(), text)

    def test_fill_phases_refuses_a_plan_item_missing_from_phases_yml(self):
        text = parent_text("i0001", ["research", "nope"], copy=None)
        self.propose("i0001", text)
        result = self.fill()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("nope", result.stderr)
        self.assertEqual(self.read(), text)

    def test_plan_order_needs_a_known_proposal_and_fill_phases(self):
        self.propose("i0001", parent_text("i0001", ["research"], copy=None))
        alone = self.ccnavi("--plan-order", "i0001")
        self.assertNotEqual(alone.returncode, 0)
        self.assertIn("--fill-phases", alone.stderr)
        stray = self.ccnavi("--fill-phases")
        self.assertNotEqual(stray.returncode, 0)
        self.assertIn("--plan-order", stray.stderr)
        unknown = self.fill("i0002")
        self.assertNotEqual(unknown.returncode, 0)
        self.assertIn("i0002", unknown.stderr)
        with_agree = self.ccnavi("--agree", "--plan-order", "i0001", "--fill-phases")
        self.assertNotEqual(with_agree.returncode, 0)
        self.assertIn("--agree", with_agree.stderr)
        self.assertNotIn("phases:", self.read())

    def test_the_agent_may_type_fill_phases(self):
        """`--agree` の外の独立したフラグなので、組み込みの止めに当たらない。"""
        rule = phase_forms.ticket_approval_rule("", self.root)
        for command in (
            "ccnavi --plan-order i0001 --fill-phases",
            ".ccnavi/bin/ccnavi --plan-order i0001 --fill-phases",
        ):
            with self.subTest(command=command):
                self.assertIsNone(rule.compiled.search(command))
                self.assertEqual(phase_forms.human_path_form(command), ("", ""))


class LimitTest(unittest.TestCase):
    """6. 定義の `scope` と `deliverables` は 20 件まで。`phases.yml` も写しも同じ。"""

    def test_scope_and_deliverables_are_capped(self):
        many = [f"d{i}/*" for i in range(21)]
        for key in ("scope", "deliverables"):
            with self.subTest(key=key):
                body = {"kind": "work", "title": "多い", key: many}
                text = yaml.safe_dump({"version": 1, "phases": {"big": body}})
                types, problems = phasetypes.parse(text)
                self.assertIsNone(types)
                self.assertTrue(any("上限 20 件" in p.detail for p in problems), problems)
                copied, problems = phasetypes.read_copy({"big": body})
                self.assertNotIn("big", copied)
                self.assertTrue(any("上限 20 件" in p.detail for p in problems), problems)
        body = {"kind": "work", "title": "ちょうど", "scope": many[:20], "deliverables": many[:20]}
        types, problems = phasetypes.parse(yaml.safe_dump({"version": 1, "phases": {"ok": body}}))
        self.assertEqual(problems, [])
        self.assertIn("ok", types)


FIXTURE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures", "parent-phases.json")


class CopyRuleTableTest(unittest.TestCase):
    """承認で写しを確かめる決まりを、見本の表（tests/fixtures/parent-phases.json）で固定する。"""

    def test_the_rules_match_the_table(self):
        from ccnavi.policy import rules
        from ccnavi.tickets import agree_candidates

        with open(FIXTURE, encoding="utf-8") as f:
            table = json.load(f)
        config, problems = phasetypes.parse(
            yaml.safe_dump({"version": 1, "phases": table["phases_yml"]}, allow_unicode=True)
        )
        self.assertEqual(problems, [])
        for case in table["cases"]:
            with self.subTest(case["name"]):
                front = {"version": 1, "ticket": "i0001"}
                if case["phases"] is not None:
                    front["phases"] = case["phases"]
                front["plan"] = case["plan"]
                front.update({"title": "親", "allow": [{"match": "Write|Edit", "glob": "src/*"}]})
                text = "---\n" + yaml.safe_dump(front, allow_unicode=True) + "---\n本文\n"
                t, problems = ticket_mod.parse(text)
                self.assertIsNotNone(t, problems)
                found = agree_candidates.plan_problems(t, config)
                for severity, key in (
                    (rules.SEVERITY_ERROR, "errors"),
                    (rules.SEVERITY_WARN, "warns"),
                ):
                    said = [p.detail for p in found if p.severity == severity]
                    if not case[key]:
                        self.assertEqual(said, [], key)
                    for phrase in case[key]:
                        self.assertTrue(any(phrase in d for d in said), (phrase, said))


if __name__ == "__main__":
    unittest.main()
