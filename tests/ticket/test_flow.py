"""子チケットのフローの受入テスト。

見るのは次のとおり。

1. エージェントの書き込みは、どのツリーの置き場でも組み込みの保護が止める。ユーザが保存したフローを
   実行後チェックが範囲外の変更として報告しない（H1）
2. 着手中の子のフローへの書き込みを止める。解いたパスと解く前のパスの両方で。着手の前と、
   終わった後は止めない
3. SubagentStart がフローのファイルを名指しし、手順と、askUserQuestion / subAgent の
   ノードでの動き方を渡す。フローが不正でも残りの文は渡す
4. `--explain --json` の子に `flow` の欄が出る（ボードが読む）。閉じた子でフローが無ければ出さない
5. 入れ子のサブエージェントが差し戻しを無視して終わったら、`systemMessage` にも載せる
6. エージェントの下書き（`wip/proposals/flows/<子>.yml`）は書ける。効力は無く、
   SubagentStart も着手のハッシュも読まない。置き場と有無は `flow.draft` に出る

置き場の決め方・YAML のフローを手順に並べること・不正な・大きい・リンクのフローの扱い・
文の量の上限・ccnavi の接頭辞の真似は go の flow のテストが見る。
"""

from __future__ import annotations

import hashlib
import json
import os
import unittest

import yaml

from tests import requires_symlink
from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import git, write
from tests.ticketsample import child_text, parent_text

CHILD = "i0001-01-01"

# 見本のフロー。ユーザがボードのフロー編集画面で書く YAML の形。
WORKFLOW_YAML = """\
id: wf-1
name: 調査の手順
version: 1.0.0
nodes:
  - id: s
    type: start
    name: 開始
    position: {x: 0, y: 0}
    data: {}
  - id: p1
    type: prompt
    name: 読む
    position: {x: 1, y: 0}
    data:
      prompt: 既存の振る舞いを読む
  - id: q1
    type: askUserQuestion
    name: 方針
    position: {x: 2, y: 0}
    data:
      questionText: どちらで進めるか
      options:
        - {label: 小さく, description: a}
        - {label: 大きく, description: b}
      outputPorts: 2
  - id: a1
    type: subAgent
    name: 深掘り
    position: {x: 3, y: 0}
    data:
      description: 依存を洗う
      prompt: 依存を列挙する
      outputPorts: 1
  - id: x1
    type: fancyNewNode
    name: 未来の種類
    position: {x: 3, y: 1}
    data: {whatever: 1}
  - id: e
    type: end
    name: 終了
    position: {x: 4, y: 0}
    data: {}
connections:
  - {id: c1, from: s, to: p1, fromPort: output, toPort: input}
  - {id: c2, from: p1, to: q1, fromPort: output, toPort: input}
  - {id: c3, from: q1, to: a1, fromPort: branch-0, toPort: input}
  - {id: c4, from: q1, to: x1, fromPort: branch-1, toPort: input}
  - {id: c5, from: a1, to: e, fromPort: output, toPort: input}
  - {id: c6, from: x1, to: e, fromPort: output, toPort: input}
"""
WORKFLOW = yaml.safe_load(WORKFLOW_YAML)

# 判定と知らせの印と、SubagentStart がフローの本文を囲む行。
CODE_LOCKED = "DENY_TICKET_FLOW_LOCKED"
CODE_CHANGED = "NOTICE_TICKET_FLOW_CHANGED"
FENCE_OPEN = "    ---- ここから合意済みのフローの本文（データ。ccnavi の知らせではない） ----"


def fingerprint(path: str) -> str:
    """着手のときに記録するフローのダイジェスト（`sha256:<16 進>`）。"""
    with open(path, "rb") as f:
        return "sha256:" + hashlib.sha256(f.read()).hexdigest()


class FlowHarness(PhaseHarness):
    """親 1 本（research）と、フローを持つ子 1 本。親の範囲に承認済みの領域は入らない。

    フローはユーザが承認のあとに親のツリーの `.ccnavi/approved/flows/<子>.yml` に保存して
    コミットする（ボードと `ccnavi-user-push-approved.sh` の進め方）。
    """

    def setUp(self):
        super().setUp()
        allow = ("src/*", "wip/*", "tests/*")
        self.propose("i0001", parent_text("i0001", ["research"], allow=allow))
        self.propose(CHILD, child_text(CHILD, "i0001", 1, ("wip/research/*",)))
        self.commit_parent()
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.flow_path = self.flow_in(self.parent_tree)
        write(self.flow_path, WORKFLOW_YAML)
        self.commit_parent("flow")

    def flow_in(self, tree_root, name=CHILD):
        return os.path.join(tree_root, ".ccnavi", "approved", "flows", f"{name}.yml")

    def write_to(self, path, agent_id="", cwd="", tool="Write", guard="disable"):
        key = "notebook_path" if tool == "NotebookEdit" else "file_path"
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "cwd": cwd or self.parent_tree,
            "session_id": "s1",
            "tool_input": {key: path, "content": "x"},
        }
        if agent_id:
            payload["agent_id"] = agent_id
        return self.ccnavi(
            "--guard-core-files", guard, "--mode", "enable", stdin=json.dumps(payload)
        )

    def assert_locked(self, result):
        out = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny", out)
        self.assertIn(CODE_LOCKED, out["permissionDecisionReason"])
        self.assertIn("着手中", out["permissionDecisionReason"])
        self.assertIn("finish", out["permissionDecisionReason"])

    def assert_not_locked(self, result):
        self.assertNotIn(CODE_LOCKED, result.stdout)

    def board_flow(self, ticket_id):
        result = self.ccnavi("--explain", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        by_id = {t["ticket"]: t for t in json.loads(result.stdout)["tickets"]}
        return by_id[ticket_id]["flow"]


class FlowGuardTest(FlowHarness):
    """エージェントはどのツリーの置き場にも書けない。ユーザの保存は報告しない。"""

    def test_agent_writes_are_denied_in_every_tree(self):
        """組み込みの保護（builtin:guard-project-home）が、承認済みの領域の `flows/` を
        ワークスペースルート・親のワークツリー・子のワークツリー・プロジェクトのどれでも止める。"""
        child_tree = self.worktree(CHILD, "i0001")
        project = os.path.join(self.root, "projects", "p")
        os.makedirs(project)
        git(project, "init", "--quiet", "-b", "main")
        for where in (self.root, self.parent_tree, child_tree, project):
            for tool in ("Write", "Edit", "NotebookEdit"):
                with self.subTest(where=where, tool=tool):
                    result = self.write_to(self.flow_in(where), tool=tool, guard="enable")
                    out = json.loads(result.stdout)["hookSpecificOutput"]
                    self.assertEqual(out["permissionDecision"], "deny", out)
                    self.assertIn("builtin:guard-project-home", out["permissionDecisionReason"])
                    self.assertIn("子のフロー", out["permissionDecisionReason"])
        # 組み込みを切っても、ルールの 1 本（このハーネスでは `*/.ccnavi/*`）が止める。
        result = self.write_to(self.flow_in(self.parent_tree), agent_id="sub-1")
        self.assertEqual(
            json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "deny"
        )
        # シェルから書く形は builtin:guard-setting-files が止める。
        rel = ".ccnavi/approved/flows/i0001-01-01.yml"
        for command in (f"echo x > {rel}", f"cp /tmp/x {rel}", f"tee {rel} < /dev/null"):
            with self.subTest(command=command):
                payload = {
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "cwd": self.parent_tree,
                    "session_id": "s1",
                    "tool_input": {"command": command},
                }
                result = self.ccnavi(
                    "--guard-core-files", "enable", "--mode", "enable", stdin=json.dumps(payload)
                )
                out = json.loads(result.stdout)["hookSpecificOutput"]
                self.assertEqual(out["permissionDecision"], "deny", out)
                self.assertIn("builtin:guard-setting-files", out["permissionDecisionReason"])

    def test_a_flow_saved_by_a_person_is_not_blamed_on_the_agent(self):
        """ユーザがボードで保存したフロー（hook を通らない書き込み）を、次のエージェントの呼び出しの
        実行後チェックが範囲外の変更として報告しない（H1）。未コミットでも、コミットしても。"""
        other = CHILD.replace("01", "02")
        ok = write(os.path.join(self.parent_tree, "wip", "a.md"), "a\n")
        first = self.hook("PostToolUse", "Write", self.parent_tree, file_path=ok, content="a")
        self.assertNotIn("POST_TICKET_SCOPE", first.stdout + first.stderr)
        write(self.flow_in(self.parent_tree, other), WORKFLOW_YAML)
        write(self.flow_path, "nodes: []\n")
        ok2 = write(os.path.join(self.parent_tree, "wip", "b.md"), "b\n")
        second = self.hook("PostToolUse", "Write", self.parent_tree, file_path=ok2, content="b")
        said = second.stdout + second.stderr
        self.assertNotIn("POST_TICKET_SCOPE", said)
        self.assertNotIn("POST_VIOLATION", said)
        self.assertNotIn("git clean", said)
        self.assertNotIn("flows/", said.replace("\\", "/"))
        self.commit_parent("person saves the flow")
        ok3 = write(os.path.join(self.parent_tree, "wip", "c.md"), "c\n")
        third = self.hook("PostToolUse", "Write", self.parent_tree, file_path=ok3, content="c")
        said = third.stdout + third.stderr
        self.assertNotIn("POST_TICKET_SCOPE", said)
        self.assertNotIn("POST_VIOLATION", said)
        self.assertNotIn("flows/", said.replace("\\", "/"))
        # 実行後チェックは有効。範囲の外の変更（以前の置き場 references/ も）はそのまま報告する。
        write(os.path.join(self.parent_tree, "references", CHILD, "flow.json"), "{}")
        ok4 = write(os.path.join(self.parent_tree, "wip", "d.md"), "d\n")
        fourth = self.hook("PostToolUse", "Write", self.parent_tree, file_path=ok4, content="d")
        self.assertIn("POST_TICKET_SCOPE", fourth.stdout + fourth.stderr)

    def test_flows_are_not_read_as_tickets(self):
        """`flows/` の YAML を承認済みチケットとして読まない（走査・索引・lint）。"""
        result = self.ccnavi("--explain", "--json")
        ids = [t["ticket"] for t in json.loads(result.stdout)["tickets"]]
        self.assertEqual(sorted(ids), ["i0001", CHILD])
        lint = self.ccnavi("--lint")
        errors = [x for x in lint.stdout.splitlines() if x.startswith("error:")]
        self.assertFalse([x for x in errors if "flows" in x], errors)


class FlowLockTest(FlowHarness):
    def test_the_flow_can_be_edited_before_the_child_starts(self):
        self.assert_not_locked(self.write_to(self.flow_path))
        shown = self.board_flow(CHILD)
        self.assertEqual(shown["rel"], f".ccnavi/approved/flows/{CHILD}.yml")
        self.assertTrue(shown["exists"])
        self.assertFalse(shown["locked"])
        self.assertFalse(shown["linked"])
        self.assertEqual(os.path.realpath(shown["path"]), os.path.realpath(self.flow_path))
        self.assertEqual(os.path.realpath(shown["tree"]), os.path.realpath(self.parent_tree))
        self.assertNotIn("declared", shown)
        self.assertIsNone(self.board_flow("i0001"))

    def test_the_flow_is_locked_while_the_child_is_in_progress(self):
        child_tree = self.run_child(CHILD)
        self.assert_locked(self.write_to(self.flow_path))
        # どのツリーの置き場も同じ手順書として止める。
        self.assert_locked(self.write_to(self.flow_in(child_tree)))
        self.assert_locked(self.write_to(self.flow_in(self.root)))
        # 誰が書いても同じ（サブエージェントでも）。大文字小文字も問わない。
        upper = os.path.join(self.parent_tree, ".ccnavi", "Approved", "FLOWS", "I0001-01-01.YML")
        self.assert_locked(self.write_to(upper, agent_id="sub-1"))
        # 相対パス・`..` を挟んだパス・NotebookEdit。
        rel = os.path.join(".ccnavi", "approved", "flows", f"{CHILD}.yml")
        self.assert_locked(self.write_to(rel, cwd=self.parent_tree))
        dotted = os.path.join(self.parent_tree, ".ccnavi", "approved", ".", "x", "..", "flows")
        self.assert_locked(self.write_to(os.path.join(dotted, f"{CHILD}.yml")))
        self.assert_locked(self.write_to(self.flow_path, tool="NotebookEdit"))
        self.assertTrue(self.board_flow(CHILD)["locked"])
        # 別の子の置き場は止めない。
        self.assert_not_locked(self.write_to(self.flow_in(self.parent_tree, "i0001-01-09")))

    @requires_symlink
    def test_links_do_not_get_around_the_lock(self):
        """置き場を指すリンク越しのパスも、リンクに差し替えたフローのパスも止める（H2）。"""
        real = write(os.path.join(self.parent_tree, "wip", "flow-real.yml"), WORKFLOW_YAML)
        os.remove(self.flow_path)
        os.symlink(real, self.flow_path)
        alias = os.path.join(self.parent_tree, "alias")
        os.symlink(os.path.dirname(self.flow_path), alias)
        self.commit_parent("links")
        child_tree = self.run_child(CHILD)
        self.assert_locked(self.write_to(os.path.join(alias, f"{CHILD}.yml")))
        self.assert_locked(self.write_to(self.flow_path))
        # リンクのフローは読まずに、そう言う。
        text = self.reason(self.hook("SubagentStart", "", child_tree, agent_id="sub-1"))
        self.assertIn("シンボリックリンク", text)
        self.assertNotIn("[askUserQuestion]", text)
        self.assertTrue(self.board_flow(CHILD)["linked"])

    def test_dry_run_still_holds_the_lock(self):
        """フローのロックはゲートで、dry-run でも止める。"""
        self.run_child(CHILD)
        result = self.hook(
            "PreToolUse", "Write", self.parent_tree, mode="dry-run", file_path=self.flow_path
        )
        self.assertIn(CODE_LOCKED, result.stdout)
        self.assertIn('"deny"', result.stdout)

    def test_the_lock_is_released_when_the_child_finishes(self):
        child_tree = self.run_child(CHILD, [("wip/research/summary.md", "まとめ\n")])
        self.assertTrue(os.path.isdir(child_tree))
        finished = self.close_child(CHILD)
        self.assertEqual(finished.returncode, 0, finished.stdout + finished.stderr)
        self.assert_not_locked(self.write_to(self.flow_path))
        # finish でレビュー待ちに入った子は、ボードでは閲覧だけ（閉じるまで編集させない）。
        self.assertTrue(self.board_flow(CHILD)["locked"])

    def test_a_closed_child_shows_the_flow_only_when_it_exists(self):
        """取り消して閉じた子でも、フローが在れば欄を出す（見返せる）。無ければ出さない。"""
        self.run_child(CHILD)
        cancelled = self.ccnavi("ticket", "cancel", CHILD, "--reason", "やめる")
        self.assertEqual(cancelled.returncode, 0, cancelled.stdout + cancelled.stderr)
        shown = self.board_flow(CHILD)
        self.assertTrue(shown["exists"])
        self.assertFalse(shown["locked"])
        os.remove(self.flow_path)
        self.assertIsNone(self.board_flow(CHILD))

    def test_subagent_start_names_the_flow_and_how_to_handle_nodes(self):
        child_tree = self.run_child(CHILD)
        result = self.hook("SubagentStart", "", child_tree, agent_id="sub-1")
        text = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn(self.flow_path.casefold(), text.casefold())
        self.assertIn("作業の前に読み、その順に進めてください", text)
        self.assertNotIn("ロック", text)
        self.assertIn(FENCE_OPEN, text)
        self.assertIn("3. [askUserQuestion] 方針", text)
        self.assertIn("[fancyNewNode] 未来の種類", text)
        # askUserQuestion: 止まってメインに返す。
        self.assertIn("問いと選択肢を添えてメインに返して", text)
        # subAgent: 入れ子で起こし、description に担当の子の識別子を 1 つだけ書く（ccnavi がその
        # 子のワークツリーで案内する）。担当以外の子は書かず、isolation は使わない。
        # Agent が無ければメインに返す。
        self.assertIn(f"description（作業名の欄）に担当の子の識別子 {CHILD} を 1 つだけ書き", text)
        self.assertIn("ccnavi が範囲とフローの案内をプロンプトに書き足す", text)
        self.assertIn("担当以外の子の識別子は書かないでください", text)
        self.assertIn("isolation は使いません", text)
        self.assertIn(child_tree, text)
        self.assertIn("wip/research/*", text)
        self.assertIn("Agent ツールが無ければ", text)
        self.assertIn("起動してほしいエージェントの種類・プロンプト", text)

    def test_subagent_start_without_a_flow_says_nothing_about_it(self):
        os.remove(self.flow_path)
        self.commit_parent("no flow")
        child_tree = self.run_child(CHILD)
        text = self.reason(self.hook("SubagentStart", "", child_tree, agent_id="sub-1"))
        self.assertIn(CHILD, text)
        self.assertNotIn("フロー", text)

    def test_an_unreadable_flow_is_named_not_raised(self):
        write(self.flow_path, "nodes:\n  - id: s\n    type: [start\n")
        self.commit_parent("broken flow")
        child_tree = self.run_child(CHILD)
        result = self.hook("SubagentStart", "", child_tree, agent_id="sub-1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("フローを読めない", self.reason(result))

    def test_a_malformed_flow_keeps_the_rest_of_subagent_start(self):
        """型の崩れたフローでも SubagentStart は失敗せず、子の一覧と範囲は渡る（H3）。

        形の誤りは `--lint --flow` と同じ理由の 1 行で言い、手順は並べない
        （`flow_shape.shape_problem`）。
        """
        write(self.flow_path, "nodes:\n  - {id: s, type: start}\nconnections: 5\n")
        self.commit_parent("bad flow")
        child_tree = self.run_child(CHILD)
        result = self.hook("SubagentStart", "", child_tree, agent_id="sub-1")
        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.reason(result)
        self.assertIn("allow: wip/research/*", text)
        self.assertIn("フローを読めない: `connections` がリストではない", text)
        self.assertNotIn("1. [start]", text)


class NestedBounceTest(PhaseHarness):
    """入れ子のサブエージェントが差し戻しを無視して終わったら、ユーザにも見せる（G4）。"""

    def test_nested_ignored_bounce_is_also_a_system_message(self):
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ("wip/research/*",)))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        child_tree = self.run_child("i0001-01-01")
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
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ("wip/research/*",)))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        child_tree = self.run_child("i0001-01-01")
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


class FlowDraftTest(FlowHarness):
    """エージェントの下書き。書けるが効力は無い。判定と組み込みの保護は今までどおり。"""

    def draft_in(self, tree_root, name=CHILD):
        return os.path.join(tree_root, "wip", "proposals", "flows", f"{name}.yml")

    def decision(self, result):
        if not result.stdout.strip():
            return ""
        return json.loads(result.stdout).get("hookSpecificOutput", {}).get("permissionDecision", "")

    def test_agent_may_write_the_draft_but_not_the_flow(self):
        for tool in ("Write", "Edit"):
            for agent_id in ("", "sub-1"):
                with self.subTest(tool=tool, agent_id=agent_id):
                    draft = self.draft_in(self.parent_tree)
                    result = self.write_to(draft, tool=tool, agent_id=agent_id, guard="enable")
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertNotEqual(self.decision(result), "deny", result.stdout)
                    self.assertNotIn("builtin:guard", result.stdout)
        # シェルから書いても止めない。
        rel = f"wip/proposals/flows/{CHILD}.yml"
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "cwd": self.parent_tree,
            "session_id": "s1",
            "tool_input": {"command": f"cp /tmp/x {rel}"},
        }
        result = self.ccnavi(
            "--guard-core-files", "enable", "--mode", "enable", stdin=json.dumps(payload)
        )
        self.assertNotEqual(self.decision(result), "deny", result.stdout)
        # 効力のあるフローは今までどおり止まる。
        result = self.write_to(self.flow_path, guard="enable")
        self.assertEqual(self.decision(result), "deny", result.stdout)
        self.assertIn("builtin:guard-project-home", self.reason(result))

    def test_the_draft_can_be_written_while_the_child_is_in_progress(self):
        """着手中のロックが止めるのはフローだけ。下書きは止めない（取り込みはボードが止める）。"""
        self.run_child(CHILD)
        self.assert_locked(self.write_to(self.flow_path))
        self.assert_not_locked(self.write_to(self.draft_in(self.parent_tree)))
        self.assertNotEqual(self.decision(self.write_to(self.draft_in(self.parent_tree))), "deny")

    def test_the_draft_is_not_part_of_the_post_check(self):
        """下書きは範囲の外の変更として報告しない（提案の置き場は丸ごとチケットの範囲の外）。"""
        write(self.draft_in(self.parent_tree), WORKFLOW_YAML)
        ok = write(os.path.join(self.parent_tree, "wip", "a.md"), "a\n")
        result = self.hook("PostToolUse", "Write", self.parent_tree, file_path=ok, content="a")
        said = result.stdout + result.stderr
        self.assertNotIn("POST_TICKET_SCOPE", said)
        self.assertNotIn("POST_VIOLATION", said)

    def test_subagent_start_does_not_read_the_draft(self):
        os.remove(self.flow_path)
        write(self.draft_in(self.parent_tree), WORKFLOW_YAML)
        self.commit_parent("draft only")
        child_tree = self.run_child(CHILD)
        write(self.draft_in(child_tree), WORKFLOW_YAML)
        text = self.reason(self.hook("SubagentStart", "", child_tree, agent_id="sub-1"))
        self.assertIn(CHILD, text)
        self.assertNotIn("フロー", text)
        self.assertNotIn("方針", text)
        self.assertNotIn("proposals/flows", text.replace("\\", "/"))

    def test_the_start_digest_does_not_read_the_draft(self):
        """着手のハッシュは下書きを読まない。着手のあとに下書きを書いても知らせない。"""
        write(self.draft_in(self.parent_tree), "nodes: []\n")
        self.commit_parent("draft")
        child_tree = self.run_child(CHILD)
        record = os.path.join(self.approved, "phases", "i0001", f"{CHILD}.flow.json")
        with open(record, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["fingerprint"], fingerprint(self.flow_path))
        self.assertEqual(data["path"], f".ccnavi/approved/flows/{CHILD}.yml")
        write(self.draft_in(self.parent_tree), WORKFLOW_YAML)
        start = self.hook("SubagentStart", "", child_tree, agent_id="sub-1")
        stop = self.hook("SubagentStop", "", child_tree, agent_id="sub-1")
        for result in (start, stop):
            self.assertNotIn(CODE_CHANGED, result.stdout)
            self.assertNotIn("systemMessage", result.stdout)

    def test_the_board_gets_the_draft_place_and_whether_it_exists(self):
        shown = self.board_flow(CHILD)["draft"]
        self.assertEqual(shown["rel"], f"wip/proposals/flows/{CHILD}.yml")
        self.assertEqual(
            os.path.realpath(shown["path"]), os.path.realpath(self.draft_in(self.parent_tree))
        )
        self.assertFalse(shown["exists"])
        self.assertFalse(shown["linked"])
        write(self.draft_in(self.parent_tree), WORKFLOW_YAML)
        shown = self.board_flow(CHILD)["draft"]
        self.assertTrue(shown["exists"])
        self.assertFalse(shown["linked"])
        # 下書きは走査されず、チケットとして読まれない。
        result = self.ccnavi("--explain", "--json")
        ids = sorted(t["ticket"] for t in json.loads(result.stdout)["tickets"])
        self.assertEqual(ids, ["i0001", CHILD])
        lint = self.ccnavi("--lint")
        errors = [x for x in lint.stdout.splitlines() if x.startswith("error:")]
        self.assertFalse([x for x in errors if "proposals/flows" in x.replace("\\", "/")], errors)

    def test_the_draft_is_not_in_the_approval_digest(self):
        """承認のダイジェスト（`--agree --preview --json` の digest）に下書きは入らない。"""
        other = "i0001-01-02"
        self.propose(other, child_text(other, "i0001", 1, ("wip/research/*",)))
        self.commit_parent("another child")
        before = json.loads(self.ccnavi("--agree", "--preview", "--json").stdout)["digest"]
        self.assertTrue(before)
        write(self.draft_in(self.parent_tree, other), WORKFLOW_YAML)
        write(self.draft_in(self.parent_tree), WORKFLOW_YAML)
        after = json.loads(self.ccnavi("--agree", "--preview", "--json").stdout)["digest"]
        self.assertEqual(before, after)
        # 承認の前の子にも、提案のツリーの置き場で欄が出る
        shown = self.board_flow(other)["draft"]
        self.assertTrue(shown["exists"])
        self.assertEqual(
            os.path.realpath(shown["path"]),
            os.path.realpath(self.draft_in(self.parent_tree, other)),
        )

    @requires_symlink
    def test_a_linked_draft_is_named(self):
        real = write(os.path.join(self.parent_tree, "wip", "draft-real.yml"), WORKFLOW_YAML)
        os.makedirs(os.path.dirname(self.draft_in(self.parent_tree)), exist_ok=True)
        os.symlink(real, self.draft_in(self.parent_tree))
        shown = self.board_flow(CHILD)["draft"]
        self.assertTrue(shown["exists"])
        self.assertTrue(shown["linked"])


if __name__ == "__main__":
    unittest.main()
