"""子チケットのフロー（設計 9.3・9.12、ADR-0085）の受入テスト。

見るのは 6 つ。

1. `flow:` の欄を読む。相対パスで `references/` の下だけ。外れた値は warn で捨て、既定を見る
2. CC Workflow Studio の `workflow.json` の形を、順に並べた手順にする。知らない種類も落とさない
3. 着手中の子のフロー（`references/<子>/` の下と `flow:` が指すファイル）への書き込みを止める。
   着手の前と、終わった後は止めない
4. SubagentStart がフローのファイルと `flow:` を名指しし、手順と、askUserQuestion / subAgent の
   ノードでの動き方を渡す
5. `--explain --json` の子に `flow` の欄が出る（ボードが読む）
6. 入れ子のサブエージェントが差し戻しを無視して終わったら、`systemMessage` にも載せる
"""

from __future__ import annotations

import json
import os
import unittest

from ccnavi import flow, ticket
from tests.ticket.test_phases import PhaseHarness, child_text, parent_text
from tests.ticket.test_ticket import git, write

WORKFLOW = {
    "id": "wf-1",
    "name": "調査の手順",
    "version": "1.0.0",
    "nodes": [
        {"id": "s", "type": "start", "name": "開始", "position": {"x": 0, "y": 0}, "data": {}},
        {
            "id": "p1",
            "type": "prompt",
            "name": "読む",
            "position": {"x": 1, "y": 0},
            "data": {"prompt": "既存の振る舞いを読む"},
        },
        {
            "id": "q1",
            "type": "askUserQuestion",
            "name": "方針",
            "position": {"x": 2, "y": 0},
            "data": {
                "questionText": "どちらで進めるか",
                "options": [
                    {"label": "小さく", "description": "a"},
                    {"label": "大きく", "description": "b"},
                ],
                "outputPorts": 2,
            },
        },
        {
            "id": "a1",
            "type": "subAgent",
            "name": "深掘り",
            "position": {"x": 3, "y": 0},
            "data": {"description": "依存を洗う", "prompt": "依存を列挙する", "outputPorts": 1},
        },
        {
            "id": "x1",
            "type": "fancyNewNode",
            "name": "未来の種類",
            "position": {"x": 3, "y": 1},
            "data": {"whatever": 1},
        },
        {"id": "e", "type": "end", "name": "終了", "position": {"x": 4, "y": 0}, "data": {}},
    ],
    "connections": [
        {"id": "c1", "from": "s", "to": "p1", "fromPort": "output", "toPort": "input"},
        {"id": "c2", "from": "p1", "to": "q1", "fromPort": "output", "toPort": "input"},
        {"id": "c3", "from": "q1", "to": "a1", "fromPort": "branch-0", "toPort": "input"},
        {"id": "c4", "from": "q1", "to": "x1", "fromPort": "branch-1", "toPort": "input"},
        {"id": "c5", "from": "a1", "to": "e", "fromPort": "output", "toPort": "input"},
        {"id": "c6", "from": "x1", "to": "e", "fromPort": "output", "toPort": "input"},
    ],
}


def with_flow(text: str, value: str) -> str:
    """子の提案の frontmatter に `flow:` を 1 行足す。"""
    return text.replace("\nphase:", f"\nflow: {value}\nphase:", 1)


class FlowFieldTest(unittest.TestCase):
    def parse(self, value=None, parent=True):
        base = child_text("i0001-01", "i0001", 1, ("wip/research/*",))
        if value is not None:
            base = with_flow(base, value)
        if not parent:
            base = parent_text("i0001", ["research"]).replace(
                "\ntitle:", f"\nflow: {value}\ntitle:"
            )
        t, problems = ticket.parse(base)
        self.assertIsNotNone(t, problems)
        return t, [p.detail for p in problems]

    def test_declared_flow_is_read(self):
        t, problems = self.parse("references/i0001-01/flow.json")
        self.assertEqual(t.flow, "references/i0001-01/flow.json")
        self.assertEqual(t.flow_rel(), "references/i0001-01/flow.json")
        self.assertEqual(problems, [])

    def test_spelling_is_normalized(self):
        t, _ = self.parse("./references//shared\\\\a.json")
        self.assertEqual(t.flow, "references/shared/a.json")

    def test_without_the_field_the_default_place_is_used(self):
        t, _ = self.parse()
        self.assertEqual(t.flow, "")
        self.assertEqual(t.flow_rel(), "references/i0001-01/flow.json")

    def test_escaping_values_are_warned_and_ignored(self):
        for bad in (
            "../x.json",
            "references/../../x.json",
            "/etc/flow.json",
            "C:/x.json",
            "~/flow.json",
            "references/$HOME/x.json",
            "docs/flow.json",
            "references",
        ):
            t, problems = self.parse(bad)
            self.assertEqual(t.flow, "", bad)
            self.assertEqual(t.flow_rel(), "references/i0001-01/flow.json", bad)
            self.assertTrue(any("`flow`" in p for p in problems), (bad, problems))

    def test_parent_does_not_read_flow(self):
        t, problems = self.parse("references/x/flow.json", parent=False)
        self.assertEqual(t.flow_rel(), "")
        self.assertTrue(any("子だけの欄" in p for p in problems), problems)


class FlowRenderTest(unittest.TestCase):
    def test_steps_follow_the_graph_and_keep_unknown_types(self):
        lines, kinds = flow.render(WORKFLOW)
        self.assertEqual(len(lines), 6)
        self.assertTrue(lines[0].startswith("1. [start] 開始 → 2"), lines)
        self.assertIn("2. [prompt] 読む: 既存の振る舞いを読む", lines[1])
        self.assertIn("3. [askUserQuestion] 方針: 問い: どちらで進めるか", lines[2])
        self.assertIn("小さく | 大きく", lines[2])
        self.assertIn("4（小さく）", lines[2])
        self.assertIn("5（大きく）", lines[2])
        self.assertIn("[subAgent] 深掘り: 依存を洗う / プロンプト: 依存を列挙する", lines[3])
        self.assertIn("[fancyNewNode] 未来の種類", lines[4])
        self.assertIn("[end] 終了", lines[5])
        self.assertIn("fancyNewNode", kinds)

    def test_broken_shapes_do_not_raise(self):
        lines, _ = flow.render({"nodes": [{"id": "a"}, "junk", {"id": "b", "data": 3}]})
        self.assertEqual(len(lines), 2)
        lines, _ = flow.render({"nodes": [], "connections": "x"})
        self.assertEqual(lines, [])

    def test_long_flows_are_cut(self):
        nodes = [{"id": f"n{i}", "type": "prompt", "name": f"n{i}"} for i in range(50)]
        lines, _ = flow.render({"nodes": nodes, "connections": []}, limit=10)
        self.assertEqual(len(lines), 11)
        self.assertIn("ほか 40 件", lines[-1])


class FlowLockTest(PhaseHarness):
    """親 1 本（research）と、フローを持つ子 1 本。親の範囲は `references/` を含む。"""

    CHILD = "i0001-01"

    def setUp(self):
        super().setUp()
        allow = ("src/*", "wip/*", "tests/*", "references/*")
        self.propose("i0001", parent_text("i0001", ["research"], allow=allow))
        text = child_text(self.CHILD, "i0001", 1, ("wip/research/*",))
        self.propose(self.CHILD, with_flow(text, f"references/{self.CHILD}/flow.json"))
        self.flow_path = write(
            os.path.join(self.parent_tree, "references", self.CHILD, "flow.json"),
            json.dumps(WORKFLOW, ensure_ascii=False),
        )
        self.commit_parent()
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def write_to(self, path, agent_id=""):
        return self.hook(
            "PreToolUse", "Write", self.parent_tree, agent_id=agent_id, file_path=path, content="x"
        )

    def assert_locked(self, result):
        out = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny", out)
        self.assertIn(flow.CODE_LOCKED, out["permissionDecisionReason"])
        self.assertIn("着手中", out["permissionDecisionReason"])
        self.assertIn("finish", out["permissionDecisionReason"])

    def assert_not_locked(self, result):
        self.assertNotIn(flow.CODE_LOCKED, result.stdout)

    def board_flow(self, ticket_id):
        result = self.ccnavi("--explain", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        by_id = {t["ticket"]: t for t in json.loads(result.stdout)["tickets"]}
        return by_id[ticket_id]["flow"]

    def test_the_flow_can_be_edited_before_the_child_starts(self):
        self.assert_not_locked(self.write_to(self.flow_path))
        shown = self.board_flow(self.CHILD)
        self.assertEqual(shown["rel"], f"references/{self.CHILD}/flow.json")
        self.assertTrue(shown["declared"])
        self.assertTrue(shown["exists"])
        self.assertFalse(shown["locked"])
        self.assertEqual(os.path.realpath(shown["path"]), os.path.realpath(self.flow_path))
        self.assertIsNone(self.board_flow("i0001"))

    def test_the_flow_is_locked_while_the_child_is_in_progress(self):
        child_tree = self.run_child(self.CHILD)
        self.assert_locked(self.write_to(self.flow_path))
        # 置き場の下の別のファイルも、どのツリーの写しも同じ手順書として止める。
        self.assert_locked(
            self.write_to(os.path.join(self.parent_tree, "references", self.CHILD, "notes.md"))
        )
        self.assert_locked(
            self.write_to(os.path.join(child_tree, "references", self.CHILD, "flow.json"))
        )
        self.assert_locked(
            self.write_to(os.path.join(self.root, "references", self.CHILD, "flow.json"))
        )
        # 誰が書いても同じ（サブエージェントでも）。大文字小文字も問わない。
        self.assert_locked(
            self.write_to(
                os.path.join(self.parent_tree, "References", self.CHILD, "flow.json"),
                agent_id="sub-1",
            )
        )
        self.assertTrue(self.board_flow(self.CHILD)["locked"])
        # 別の子の置き場は止めない。
        self.assert_not_locked(
            self.write_to(os.path.join(self.parent_tree, "references", "i0001-09", "flow.json"))
        )

    def test_dry_run_only_reports_the_lock(self):
        self.run_child(self.CHILD)
        result = self.hook(
            "PreToolUse", "Write", self.parent_tree, mode="dry-run", file_path=self.flow_path
        )
        self.assertIn(flow.CODE_LOCKED, result.stdout)
        self.assertNotIn('"deny"', result.stdout)

    def test_the_lock_is_released_when_the_child_finishes(self):
        child_tree = self.run_child(self.CHILD, [("wip/research/summary.md", "まとめ\n")])
        self.assertTrue(os.path.isdir(child_tree))
        finished = self.close_child(self.CHILD)
        self.assertEqual(finished.returncode, 0, finished.stdout + finished.stderr)
        self.assert_not_locked(self.write_to(self.flow_path))
        self.assertFalse(self.board_flow(self.CHILD)["locked"])

    def test_subagent_start_names_the_flow_and_how_to_handle_nodes(self):
        child_tree = self.run_child(self.CHILD)
        result = self.hook("SubagentStart", "", child_tree, agent_id="sub-1")
        text = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn(self.flow_path, text)
        self.assertIn("`flow:`", text)
        self.assertIn("読み直す", text)
        self.assertIn("3. [askUserQuestion] 方針", text)
        self.assertIn("[fancyNewNode] 未来の種類", text)
        # askUserQuestion: 止まってメインに返す。
        self.assertIn("問いと選択肢を添えてメインに返す", text)
        # subAgent: 入れ子で起こし、担当の子・ワークツリー・範囲を必ず書く。Agent が無ければ返す。
        self.assertIn(f"担当の子チケット {self.CHILD}", text)
        self.assertIn(child_tree, text)
        self.assertIn("wip/research/*", text)
        self.assertIn("他の子のワークツリーには触れない", text)
        self.assertIn("Agent ツールが無ければ", text)
        self.assertIn("起動してほしいエージェントの種類・プロンプト", text)

    def test_subagent_start_without_a_flow_says_nothing_about_it(self):
        os.remove(self.flow_path)
        self.commit_parent("no flow")
        child_tree = self.run_child(self.CHILD)
        text = self.reason(self.hook("SubagentStart", "", child_tree, agent_id="sub-1"))
        self.assertIn(self.CHILD, text)
        # `flow:` を書いたのにファイルが無いことは言う。
        self.assertIn("が無い", text)
        self.assertNotIn("askUserQuestion", text)

    def test_an_unreadable_flow_is_named_not_raised(self):
        write(self.flow_path, "{not json")
        self.commit_parent("broken flow")
        child_tree = self.run_child(self.CHILD)
        result = self.hook("SubagentStart", "", child_tree, agent_id="sub-1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("フローを読めない", self.reason(result))


class NestedBounceTest(PhaseHarness):
    """入れ子のサブエージェントが差し戻しを無視して終わったら、人にも見せる（G4）。"""

    def test_nested_ignored_bounce_is_also_a_system_message(self):
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ("wip/research/*",)))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        child_tree = self.run_child("i0001-01")
        write(os.path.join(child_tree, "src", "stray.py"), "x\n")
        git(child_tree, "add", "-A")
        git(child_tree, "commit", "--quiet", "-m", "stray")
        stop = self.hook("SubagentStop", "", child_tree, agent_id="grand-1")
        self.assertEqual(stop.returncode, 2, stop.stdout + stop.stderr)

        def after(agent_id):
            payload = {
                "hook_event_name": "PostToolUse",
                "tool_name": "Agent",
                "cwd": self.parent_tree,
                "session_id": "s1",
                "tool_input": {"description": "孫"},
                "tool_response": {"agentId": "grand-1"},
            }
            if agent_id:
                payload["agent_id"] = agent_id
            return self.ccnavi("--mode", "enable", stdin=json.dumps(payload))

        nested = after("child-1")
        out = json.loads(nested.stdout)
        self.assertIn("差し戻されたまま", out["hookSpecificOutput"]["additionalContext"])
        self.assertIn("差し戻されたまま", out.get("systemMessage", ""))

    def test_top_level_launch_keeps_the_message_for_the_model_only(self):
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ("wip/research/*",)))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        child_tree = self.run_child("i0001-01")
        write(os.path.join(child_tree, "src", "stray.py"), "x\n")
        git(child_tree, "add", "-A")
        git(child_tree, "commit", "--quiet", "-m", "stray")
        self.assertEqual(self.hook("SubagentStop", "", child_tree, agent_id="sub-9").returncode, 2)
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Agent",
            "cwd": self.parent_tree,
            "session_id": "s1",
            "tool_input": {"description": "子"},
            "tool_response": {"agentId": "sub-9"},
        }
        out = json.loads(self.ccnavi("--mode", "enable", stdin=json.dumps(payload)).stdout)
        self.assertIn("差し戻されたまま", out["hookSpecificOutput"]["additionalContext"])
        self.assertNotIn("systemMessage", out)


if __name__ == "__main__":
    unittest.main()
