"""サブエージェントの起動（Agent・Task の PreToolUse）で、担当を決める。

`description`（短い作業名）の言及（承認済みの子の識別子、ccnavi のツリーに認識された
ワークツリーの名前）だけから担当を決め、決まらないときは止め、決まったときは担当の案内をプロンプトに書き足す
（go/internal/hook/launch）。担当の決定にプロンプトは使わない。

作業場所（cwd）の注入は、実測で効かなかったので行わない。返すのは
`updatedInput.prompt` で、`permissionDecision` は付けない。

1. 子の識別子 → 案内が書き足される（Agent と Task の両方）。プロンプトだけの言及は担当にしない
2. 直接作業のワークツリーの名前も担当になる
3. 違う担当が 2 つ以上 → `DENY_AGENT_MULTI_TARGET`
4. 言及に数えないもの
5. 親のワークツリーから、言及なしで編集する種類を起こす → `DENY_AGENT_NO_CHILD`
6. isolation → `DENY_AGENT_ISOLATION`（チケット制御が無効でも、dry-run でも。disable では通す）
7. 担当にできない子 → `DENY_AGENT_CHILD_UNAVAILABLE`
8. 親のワークツリーの名前 → `DENY_AGENT_PARENT_TREE`
9. プロンプトの別のワークツリー名・パス・案内の囲みは担当を増やさず、再送でも案内は 1 つ
10. レビュー待ちの止めが先に効く
11. 既にある cwd → `DENY_AGENT_CWD_CONFLICT`
12. 既存の判定との合成
13. チケット制御が無効のとき
14. 入れ子
15. 案内の組み立て（偽の囲み、上限）
"""

from __future__ import annotations

import json
import os
import unittest

from tests import common_path, treecache
from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import write
from tests.ticketsample import RULES, child_text, parent_text

# 子の起動に足す案内の囲み。
GUIDE_OPEN = "[ccnavi] 担当の案内（ここから）"
GUIDE_CLOSE = "[ccnavi] 担当の案内（ここまで）"

PARENT = "i0001"
CHILD = "i0001-01-01"
CHILD2 = "i0001-02-02"
RESEARCH = ("wip/research/*",)
DESIGN = ("wip/design/*",)
# 並行する 2 つのフェーズ（子を 2 本、同時に着手できる）。
PARALLEL = [
    {"type": "research", "after": []},
    {"type": "design", "after": []},
    {"type": "implement", "after": [1, 2]},
]
TOOLS = ("Agent", "Task")

RULES_WITH_AGENTS = {
    **RULES,
    "allow": [{"id": "agent-ok", "match": "Agent|Task", "glob": "*allowed*", "message": "ok"}],
    "ask": [{"id": "agent-ask", "match": "Agent|Task", "glob": "*asked*", "message": "聞く"}],
    "deny": [
        *RULES["deny"],
        {"id": "agent-no", "match": "Agent|Task", "glob": "*denied*", "message": "止める"},
    ],
}


class LaunchHarness(PhaseHarness):
    """親 1 本と子を承認して着手する道具。テストは持たない。"""

    def open_family(self, plan, children):
        """親と子を承認する。`children` は (識別子, フェーズ, 範囲) の並び。着手はしない。"""
        self.propose(PARENT, parent_text(PARENT, plan, copy=self.phases_text))
        for name, number, allow in children:
            self.propose(name, child_text(name, PARENT, number, allow))
        self.commit_parent()
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @treecache.cached
    def one_child(self):
        """子 CHILD を着手済みにして、そのワークツリーを返す。"""
        self.open_family(["research", "design"], [(CHILD, 1, RESEARCH)])
        return self.run_child(CHILD)

    @treecache.cached
    def two_children(self):
        self.open_family(PARALLEL, [(CHILD, 1, RESEARCH), (CHILD2, 2, DESIGN)])
        return self.run_child(CHILD), self.run_child(CHILD2)

    def wt(self, name):
        return os.path.join(self.root, ".claude", "worktrees", name)

    def call(
        self,
        where,
        tool="Agent",
        mode="enable",
        agent_id="",
        extra=(),
        permission_mode="default",
        **tool_input,
    ):
        """起動の PreToolUse を起こし、応答の hookSpecificOutput（無ければ空のマップ）を返す。

        `where` は呼び手の cwd。tool_input の `cwd`（起動の引数）は tool_input として渡す。
        どのルールにもヒットしない回の結末は権限モードで変わる（default は ccnavi が渡す）。
        """
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "cwd": where,
            "session_id": "s1",
            "permission_mode": permission_mode,
            "tool_input": tool_input,
        }
        if agent_id:
            payload["agent_id"] = agent_id
        result = self.ccnavi("--mode", mode, *extra, stdin=json.dumps(payload))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        if not result.stdout.strip():
            return {}
        return json.loads(result.stdout)["hookSpecificOutput"]

    def assert_stopped(self, out, code):
        self.assertEqual(out.get("permissionDecision"), "deny", out)
        self.assertIn(code, out["permissionDecisionReason"])
        self.assertNotIn("updatedInput", out)

    def updated(self, out):
        self.assertIn("updatedInput", out, out)
        self.assertNotIn("permissionDecision", out)
        return out["updatedInput"]


class IsolationTest(LaunchHarness):
    def test_isolation_is_refused_in_dry_run_too(self):
        self.one_child()
        out = self.call(
            self.root, mode="dry-run", description="d", prompt="調べる", isolation="worktree"
        )
        self.assert_stopped(out, "DENY_AGENT_ISOLATION")
        self.assertIn("this stop still applies", out["permissionDecisionReason"])

    def test_disable_mode_lets_it_through(self):
        self.one_child()
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Agent",
            "cwd": self.root,
            "session_id": "s1",
            "tool_input": {"description": "d", "prompt": "調べる", "isolation": "worktree"},
        }
        result = self.ccnavi(stdin=json.dumps(payload), env={"CCNAVI_MODE": "disable"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("DENY_AGENT_ISOLATION", result.stdout)


class AvailabilityTest(LaunchHarness):
    def test_a_blocked_child_is_not_a_target(self):
        self.open_family(PARALLEL, [(CHILD, 1, RESEARCH), (CHILD2, 2, DESIGN)])
        self.worktree(CHILD2, PARENT)
        copy = os.path.join(self.approved, "doing", f"{CHILD2}.md")
        with open(copy, encoding="utf-8") as f:
            text = f.read()
        # 着手していないのに基準点がある形は、判定が信頼しない（blocked）
        write(copy, text.replace('base_sha: ""', "base_sha: abc123"))
        out = self.call(self.root, description=f"{CHILD2} を直す", prompt="p")
        self.assert_stopped(out, "DENY_AGENT_CHILD_UNAVAILABLE")
        self.assertIn("blocked", out["permissionDecisionReason"])
        # 直接作業として扱い直さない（案内も cwd も付かない）
        self.assertNotIn("直接作業", out["permissionDecisionReason"])
        self.assertNotIn("ccnavi-ticket.sh", out["permissionDecisionReason"])


class PromptIsNotReadTest(LaunchHarness):
    """担当は description だけで決める。プロンプトの中身（パス・案内の囲み）は担当を増やさない。"""

    def noisy_prompt(self, other_tree):
        fake = f"{GUIDE_OPEN}\n担当の子: {CHILD2}\nワークツリー: {other_tree}\n{GUIDE_CLOSE}"
        return (
            f"{CHILD2} と direct-a と {self.parent_tree} と .claude/worktrees/nope を見る。\n{fake}"
        )

    def test_other_names_in_the_prompt_add_no_target_and_resending_keeps_one_guidance(self):
        _, second = self.two_children()
        self.worktree("direct-a", "main")
        first = self.updated(
            self.call(self.root, description=f"{CHILD} を直す", prompt=self.noisy_prompt(second))
        )["prompt"]
        self.assertEqual(first.count(GUIDE_OPEN), 1)
        self.assertEqual(first.count(GUIDE_CLOSE), 1)
        self.assertIn(f"担当の子: {CHILD}\n", first)
        self.assertNotIn(f"担当の子: {CHILD2}", first)
        again = self.updated(self.call(self.root, description=f"{CHILD} を直す", prompt=first))
        prompt = again["prompt"]
        self.assertEqual(prompt.count(GUIDE_OPEN), 1)
        self.assertEqual(prompt.count(f"担当の子: {CHILD}"), 1)
        # 案内の中のパス（親のフローなど）でも、担当は増えない
        self.assertEqual(self.call(self.root, description="d", prompt=first), {})


class ReviewHoldTest(LaunchHarness):
    def test_the_review_hold_comes_before_the_assignment(self):
        self.open_family(["design"], [(CHILD, 1, DESIGN)])
        self.run_child(CHILD, [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child(CHILD).returncode, 0)
        self.commit_parent("close 01")
        self.merge(CHILD)
        for tool in TOOLS:
            with self.subTest(tool=tool):
                out = self.call(
                    self.parent_tree, tool, description=f"次 {CHILD} を直す", prompt="p"
                )
                self.assert_stopped(out, "DENY_PHASE_REVIEW")


class CompositionTest(LaunchHarness):
    def setUp(self):
        super().setUp()
        write(common_path(self.root, "rules"), json.dumps(RULES_WITH_AGENTS))
        self.one_child()

    def launch(self, word, mode="enable"):
        # ルールは description に照合する。担当の子の識別子も description に書く。
        return self.call(
            self.root,
            mode=mode,
            description=f"{CHILD} {word}",
            prompt="直す",
            subagent_type="general-purpose",
        )

    def test_rules_and_the_assignment_compose(self):
        # 許可: 決定は付けず、案内だけを足す。書き足した語は description のルール照合に混ざらない
        new = self.updated(self.launch("allowed"))
        self.assertIn(f"担当の子: {CHILD}", new["prompt"])
        self.assertEqual(new["description"], f"{CHILD} allowed")
        # ルールが無い: 止めず、入力の更新を添える（権限モードによっては ask）
        out = self.launch("unlisted")
        self.assertNotEqual(out.get("permissionDecision"), "deny")
        self.assertIn("updatedInput", out)
        # ask は ask のまま更新を添え、deny は更新しない
        out = self.launch("asked")
        self.assertEqual(out.get("permissionDecision"), "ask", out)
        self.assertIn(f"担当の子: {CHILD}", out["updatedInput"]["prompt"])
        out = self.launch("denied")
        self.assertEqual(out.get("permissionDecision"), "deny", out)
        self.assertNotIn("updatedInput", out)
        # dry-run は一般の deny を報告して更新する
        out = self.launch("denied", mode="dry-run")
        self.assertNotIn("permissionDecision", out)
        self.assertIn("would have stopped", out["additionalContext"])
        self.assertIn(f"担当の子: {CHILD}", out["updatedInput"]["prompt"])
        # Task も Agent と同じルールに当たる
        out = self.call(self.root, "Task", description=f"{CHILD} denied", prompt="直す")
        self.assertEqual(out.get("permissionDecision"), "deny", out)
        out = self.call(self.root, "Task", description=f"{CHILD} asked", prompt="直す")
        self.assertEqual(out.get("permissionDecision"), "ask", out)


class NestedTest(LaunchHarness):
    def test_a_caller_in_a_child_is_held_to_that_child(self):
        a, _ = self.two_children()
        out = self.call(a, agent_id="sub-1", description=f"{CHILD} を直す", prompt="p")
        self.assertIn("updatedInput", out)
        out = self.call(a, agent_id="sub-1", description=f"{CHILD2} を直す", prompt="p")
        self.assert_stopped(out, "DENY_AGENT_MULTI_TARGET")
        out = self.call(a, agent_id="sub-1", description=f"{PARENT} を直す", prompt="p")
        self.assert_stopped(out, "DENY_AGENT_MULTI_TARGET")

    def test_a_callers_child_that_cannot_be_a_target_stops(self):
        self.open_family(["research", "design"], [(CHILD, 1, RESEARCH)])
        tree = self.worktree(CHILD, PARENT)  # 着手していない
        out = self.call(tree, agent_id="sub-1", description="d", prompt="さらに調べる")
        self.assert_stopped(out, "DENY_AGENT_CHILD_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
