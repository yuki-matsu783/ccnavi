"""承認の事実を hook がモデルへ 1 度だけ伝えることの受入テスト。

設計 wip/design/approve-popup.md §2.4。人がボードで承認したあと、モデルは次の
UserPromptSubmit か PreToolUse で「写しが置かれた。後工程を進める」を読む。見るのは 5 つ。

1. 承認の後の UserPromptSubmit で `additionalContext` に文が載り、もう 1 度は載らない
2. PreToolUse（allow になる呼び出し）でも同じ文が 1 度だけ載る
3. セッションの最初の hook の時点で既にあった写しは伝えない（起点）
4. 別のセッションにはそれぞれ 1 度ずつ伝える。サブエージェントには伝えない
5. 控えを置けない（`--state ""`）ときは伝えず、控えも作らない

文は `--approve --yes` の `prompt` と同じもの（同じ関数から出る）。
"""

from __future__ import annotations

import glob
import json
import os
import unittest

from tests.test_phases import PhaseHarness, child_text, parent_text


class ApprovalNewsTest(PhaseHarness):
    # ---- 道具

    def event(self, event, session="s1", agent_id="", tool="", state=None, **tool_input):
        payload = {"hook_event_name": event, "cwd": self.parent_tree, "session_id": session}
        if tool:
            payload["tool_name"] = tool
            payload["tool_input"] = tool_input
        if agent_id:
            payload["agent_id"] = agent_id
        extra = ["--state", state] if state is not None else []
        result = self.ccnavi("--mode", "enable", *extra, stdin=json.dumps(payload))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def context(self, result):
        if not result.stdout.strip():
            return ""
        out = json.loads(result.stdout).get("hookSpecificOutput", {})
        return out.get("additionalContext") or ""

    def prompt(self, session="s1", **kw):
        return self.context(self.event("UserPromptSubmit", session=session, **kw))

    def before(self, session="s1", **kw):
        target = os.path.join(self.parent_tree, "src", "x.py")
        return self.context(
            self.event("PreToolUse", session=session, tool="Write", file_path=target, **kw)
        )

    def approve_yes(self, tickets):
        result = self.ccnavi("--approve", "--yes", ",".join(tickets), "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)["prompt"]

    def parent_only(self):
        self.propose("i0001", parent_text("i0001", ["research", "design"]))
        self.commit_parent()

    def next_child(self, name="i0001-01"):
        self.propose(name, child_text(name, "i0001", 1, ("wip/research/*",), False))
        self.commit_parent()

    # ---- 1. UserPromptSubmit で 1 度

    def test_prompt_after_approval_carries_the_news_once(self):
        self.parent_only()
        self.assertEqual(self.prompt(), "")  # 起点。まだ何も無い
        self.next_child()
        prompt = self.approve_yes(["i0001", "i0001-01"])
        first = self.prompt()
        self.assertIn(prompt, first)
        self.assertIn("i0001-01", first)
        self.assertEqual(self.prompt(), "")

    # ---- 2. PreToolUse でも 1 度

    def test_pre_tool_use_carries_the_news_once_alongside_the_verdict(self):
        self.parent_only()
        self.before()  # 起点
        self.next_child()
        prompt = self.approve_yes(["i0001", "i0001-01"])
        result = self.event(
            "PreToolUse", tool="Write", file_path=os.path.join(self.parent_tree, "src", "x.py")
        )
        out = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertIn(prompt, out.get("additionalContext") or "")
        # 判定は判定で返っている（承認された範囲の中なので allow のまま）。
        self.assertNotEqual(out.get("permissionDecision"), "deny")
        self.assertEqual(self.before(), "")
        # 一方で聞いたなら、もう一方でも言わない。
        self.assertEqual(self.prompt(), "")

    # ---- 3. 起点より前の写しは伝えない

    def test_copies_that_existed_at_the_first_hook_are_not_news(self):
        self.parent_only()
        self.next_child()
        self.approve_yes(["i0001", "i0001-01"])
        # このセッションの最初の hook。既にある写しは知っているものとして控える。
        self.assertEqual(self.prompt(), "")
        self.assertEqual(self.before(), "")

    def test_session_start_sets_the_baseline_without_speaking_about_it(self):
        self.parent_only()
        self.next_child()
        self.approve_yes(["i0001", "i0001-01"])
        started = self.context(self.event("SessionStart"))
        self.assertNotIn("i0001-01", started)
        self.assertEqual(self.prompt(), "")
        # 起点の後に承認されたものは伝える。SessionStart（compact の後にも来る）は起点を戻さない。
        self.next_child("i0001-02")
        self.approve_yes(["i0001-02"])
        self.event("SessionStart")
        self.assertIn("i0001-02", self.prompt())

    # ---- 4. セッションごと。サブエージェントは除く

    def test_each_session_hears_once_and_subagents_do_not(self):
        self.parent_only()
        self.prompt(session="s1")
        self.prompt(session="s2")
        self.next_child()
        self.approve_yes(["i0001", "i0001-01"])
        self.assertIn("i0001-01", self.prompt(session="s1"))
        self.assertIn("i0001-01", self.prompt(session="s2"))
        self.assertEqual(self.prompt(session="s1"), "")
        self.assertEqual(self.prompt(session="s2"), "")
        # 承認より後に起動したサブエージェントは、起動時点の写しを起点にする。
        self.assertEqual(self.before(session="s1", agent_id="sub-1"), "")
        self.assertEqual(self.before(session="s1", agent_id="sub-1"), "")

    # ---- 5. 控えを置けないときは黙る

    def test_without_a_state_dir_nothing_is_told_and_nothing_is_written(self):
        self.parent_only()
        self.prompt(state="")
        self.next_child()
        self.approve_yes(["i0001", "i0001-01"])
        self.assertEqual(self.prompt(state=""), "")
        self.assertEqual(glob.glob(os.path.join(self.state, "approved-*")), [])

    def test_the_memo_lives_next_to_the_once_memo(self):
        self.parent_only()
        self.prompt()
        files = glob.glob(os.path.join(self.state, "approved-s1-*.json"))
        self.assertEqual(len(files), 1)
        with open(files[0], encoding="utf-8") as f:
            self.assertIn("known", json.load(f))


if __name__ == "__main__":
    unittest.main()
