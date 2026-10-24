"""SubagentStart が、同じワークツリーで動く別の編集するサブエージェントを案内する（#433）。

起動したサブエージェントの `additionalContext` に、同じツリーで実行中の別の編集する
サブエージェントがいることを 1〜3 文で足す。止めない。

1. 同じツリーで編集するサブエージェントが 2 つ動けば、後から起動したほうに案内が出る
2. 先に動いていたほうには出ない
3. 読み取り専用（`Explore`、`tools` に書き込み系が無い定義）は、案内を受けず、相手にも数えない
4. 定義が無い・`tools` に制限が無いサブエージェントは、編集するものとして数える
5. ツリーが違えば出ない
6. 終わったサブエージェントは数えない
7. 再開で `SubagentStart` がまた走っても、同じ相手を重ねて案内しない。新しい相手には案内する
8. 別のセッションのサブエージェントは数えない
"""

from __future__ import annotations

import json
import os
import unittest

from ccnavi.hook import subagent
from tests.ticket.test_phases import PhaseHarness

MARK = "別の編集するサブエージェントが動いています"
EDITOR = "general-purpose"


class SharedTreeHarness(PhaseHarness):
    def start(self, agent_id, agent_type, cwd=None, session="s1", tools=None):
        """SubagentStart を起こし、渡された文（additionalContext）を返す。"""
        payload = {
            "hook_event_name": "SubagentStart",
            "cwd": cwd or self.root,
            "session_id": session,
            "agent_id": agent_id,
            "agent_type": agent_type,
        }
        if tools is not None:
            payload["tools"] = tools
        result = self.ccnavi("--mode", "enable", stdin=json.dumps(payload))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return self.reason(result)

    def stop(self, agent_id, cwd=None, session="s1"):
        payload = {
            "hook_event_name": "SubagentStop",
            "cwd": cwd or self.root,
            "session_id": session,
            "agent_id": agent_id,
        }
        result = self.ccnavi("--mode", "enable", stdin=json.dumps(payload))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def define(self, name, front):
        """`.claude/agents/<name>.md` に定義を置く。"""
        folder = os.path.join(self.root, ".claude", "agents")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, name + ".md"), "w", encoding="utf-8") as f:
            f.write(f"---\nname: {name}\n{front}---\n本文\n")


class SharedTreeTest(SharedTreeHarness):
    def test_the_later_editor_is_told_and_the_earlier_one_is_not(self):
        self.assertNotIn(MARK, self.start("a1", EDITOR))
        text = self.start("a2", EDITOR)
        self.assertIn(MARK, text)
        self.assertIn("ワークスペースルート", text)
        self.assertIn("decisions.md", text)
        self.assertIn(f"{EDITOR} 1 件", text)

    def test_a_single_editor_is_not_told(self):
        self.assertNotIn(MARK, self.start("a1", EDITOR))

    def test_read_only_agents_are_neither_told_nor_counted(self):
        self.assertNotIn(MARK, self.start("r1", "Explore"))
        self.assertNotIn(MARK, self.start("a1", EDITOR))
        self.assertNotIn(MARK, self.start("r2", "Plan"))
        self.assertIn(MARK, self.start("a2", EDITOR))

    def test_a_definition_without_write_tools_is_read_only(self):
        self.define("reader", "tools: Read, Grep, Glob\n")
        self.assertNotIn(MARK, self.start("a1", EDITOR))
        self.assertNotIn(MARK, self.start("r1", "reader"))

    def test_a_definition_with_a_write_tool_or_no_limit_is_an_editor(self):
        self.define("writer", "tools: Read, Edit\n")
        self.define("open", "description: 制限なし\n")
        self.assertNotIn(MARK, self.start("a1", "writer"))
        self.assertIn(MARK, self.start("a2", "open"))

    def test_only_confirmed_read_tools_make_a_read_only_agent(self):
        """読み取りと確かめられない一覧は、編集するものとして数える。"""
        self.start("peer", EDITOR)
        for number, tools in enumerate(
            (
                ["*"],
                ["Read", "Edit(src/**)"],
                ["Read", "Bash"],
                ["Read", "mcp__fs__write_file"],
                [],
            )
        ):
            with self.subTest(tools=tools):
                # 案内した相手は agent_id ごとに覚えるので、繰り返しごとに別の ID で起動する。
                self.assertIn(MARK, self.start(f"probe-{number}", EDITOR, tools=tools))
        self.assertNotIn(MARK, self.start("probe-read", EDITOR, tools=["read", "GREP"]))

    def test_a_definition_wins_over_a_builtin_name(self):
        self.define("Explore", "tools: Read, Write\n")
        self.assertNotIn(MARK, self.start("a1", EDITOR))
        self.assertIn(MARK, self.start("a2", "Explore"))

    def test_disallowed_tools_do_not_make_a_read_only_agent(self):
        self.define("narrow", "disallowedTools: Write, Edit, NotebookEdit\n")
        self.assertNotIn(MARK, self.start("a1", EDITOR))
        self.assertIn(MARK, self.start("a2", "narrow"))

    def test_flat_removes_every_line_break_that_splitlines_uses(self):
        """印の値に区切りが残ると、`splitlines` で読む側が途中で切れた値を受け取る。"""
        text = "a\nb\rc\x0bd\x0ce\x1cf\x1dg\x1eh\x85i j k"
        flat = subagent._flat(text)
        self.assertEqual(len(flat.splitlines()), 1, repr(flat))
        self.assertEqual(flat.split(" "), list("abcdefghijk"))

    def test_an_unknown_agent_type_counts_as_an_editor(self):
        self.assertNotIn(MARK, self.start("a1", "no-such-definition"))
        self.assertIn(MARK, self.start("a2", ""))

    def test_payload_tools_win_over_the_definition(self):
        self.assertNotIn(MARK, self.start("a1", EDITOR))
        self.assertNotIn(MARK, self.start("r1", EDITOR, tools=["Read", "Grep"]))
        self.assertIn(MARK, self.start("a2", EDITOR, tools=["Read", "Write"]))

    def test_other_trees_do_not_count(self):
        self.assertNotIn(MARK, self.start("a1", EDITOR, cwd=self.root))
        self.assertNotIn(MARK, self.start("a2", EDITOR, cwd=self.parent_tree))
        self.assertIn(MARK, self.start("a3", EDITOR, cwd=self.parent_tree))

    def test_a_stopped_agent_is_not_counted(self):
        self.start("a1", EDITOR)
        self.stop("a1")
        self.assertNotIn(MARK, self.start("a2", EDITOR))

    def test_another_session_is_not_counted(self):
        self.start("a1", EDITOR, session="s1")
        self.assertNotIn(MARK, self.start("a2", EDITOR, session="s2"))

    def test_a_resumed_start_does_not_repeat_the_same_peer(self):
        self.start("a1", EDITOR)
        self.assertIn(MARK, self.start("a2", EDITOR))
        self.assertNotIn(MARK, self.start("a2", EDITOR))

    def test_a_new_peer_is_told_after_a_resume(self):
        self.start("a1", EDITOR)
        self.assertIn(MARK, self.start("a2", EDITOR))
        self.start("a3", EDITOR)
        again = self.start("a2", EDITOR)
        self.assertIn(MARK, again)
        self.assertIn(f"{EDITOR} 1 件", again)

    def test_the_note_does_not_replace_the_other_start_texts(self):
        self.start("a1", EDITOR)
        text = self.start("a2", EDITOR)
        self.assertIn("スキル候補", text)


if __name__ == "__main__":
    unittest.main()
