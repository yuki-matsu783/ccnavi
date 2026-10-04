"""承認したことの伝わり方の受入テスト。

承認したことは、拡張が渡す文（`--agree --yes` の `prompt`）と `ccnavi-ticket.sh status` で伝わる。
hook は承認を伝えない。hook がセッションの最初で起点を取り、それより後に増えた承認を伝える形は、
セッションの開始時の取り込みで届いた承認を起点に含めて取りこぼした。見るのは 3 つ。

1. `--agree --yes` の `prompt` に、承認したチケットと次の一手が載る
2. 承認のあとの UserPromptSubmit・PreToolUse・SessionStart は承認を伝えない
3. 承認を伝えた記録（`approved-<セッション>-<エージェント>.json`）を書かない
"""

from __future__ import annotations

import glob
import json
import os
import unittest

from tests.ticket.test_phases import PhaseHarness, child_text, parent_text


class ApprovalToldTest(PhaseHarness):
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
        # ボードと同じく、見せた指紋（承認画面の本文・判定が読んだ中身・書き込む中身）を渡す。
        # 渡さない `--yes` は承認しない。
        shown = self.ccnavi("--agree", "--preview", "--json")
        digest = json.loads(shown.stdout)["digest"]
        result = self.ccnavi("--agree", "--yes", ",".join(tickets), "--digest", digest, "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)["prompt"]

    def parent_only(self):
        self.propose("i0001", parent_text("i0001", ["research", "design"]))
        self.commit_parent()

    def next_child(self, name="i0001-01"):
        self.propose(name, child_text(name, "i0001", 1, ("wip/research/*",), False))
        self.commit_parent()

    # ---- 1. 拡張が渡す文

    def test_the_prompt_names_the_approved_tickets_and_the_order_of_start(self):
        self.parent_only()
        self.next_child()
        prompt = self.approve_yes(["i0001", "i0001-01"])
        self.assertIn("i0001-01", prompt)
        # 子より先に親を着手する順も、この文で伝える（REQ-TKT-48）。
        self.assertIn("start <親>", prompt)

    def test_a_batch_without_a_new_parent_does_not_ask_for_the_parent_start(self):
        """子だけの回では、親の `start` を勧めないこと。

        勧めたコマンドは、親が着手済みなら「着手済み」で終わる。案内どおりに打って終了コード 1 を
        受け取る文は、案内ではなく誤りの元になる。子より先に親を着手する順そのものは、
        子の行の「親が未着手だと止まる」で残る。
        """
        self.parent_only()
        self.next_child()
        self.approve_yes(["i0001", "i0001-01"])
        self.next_child("i0001-02")
        told = self.approve_yes(["i0001-02"])
        self.assertNotIn("start <親>", told)
        self.assertIn("親が未着手だと止まる", told)

    def test_a_revision_is_told_by_the_prompt(self):
        self.parent_only()
        self.approve_yes(["i0001"])
        self.propose("i0001", parent_text("i0001", ["research", "design", "acceptance"]))
        self.commit_parent()
        self.assertIn("改版", self.approve_yes(["i0001"]))

    # ---- 2. hook は伝えない

    def test_hooks_do_not_tell_about_approvals(self):
        self.parent_only()
        self.assertEqual(self.prompt(), "")
        self.before()
        self.context(self.event("SessionStart"))
        self.next_child()
        self.approve_yes(["i0001", "i0001-01"])
        self.assertNotIn("承認され", self.prompt())
        self.assertNotIn("承認され", self.before())
        self.assertNotIn("承認され", self.before(session="s2", agent_id="sub-1"))
        self.assertNotIn("i0001-01", self.context(self.event("SessionStart")))
        self.assertNotIn("承認され", self.prompt(session="s2"))

    def test_the_verdict_still_comes_back_after_an_approval(self):
        self.parent_only()
        self.next_child()
        self.approve_yes(["i0001", "i0001-01"])
        result = self.event(
            "PreToolUse", tool="Write", file_path=os.path.join(self.parent_tree, "src", "x.py")
        )
        if result.stdout.strip():
            out = json.loads(result.stdout)["hookSpecificOutput"]
            self.assertNotEqual(out.get("permissionDecision"), "deny")

    # ---- 3. 記録を書かない

    def test_no_memo_of_told_approvals_is_written(self):
        self.parent_only()
        self.event("SessionStart")
        self.prompt()
        self.before()
        self.next_child()
        self.approve_yes(["i0001", "i0001-01"])
        self.prompt()
        self.before()
        self.assertEqual(glob.glob(os.path.join(self.state, "approved-*")), [])


if __name__ == "__main__":
    unittest.main()
