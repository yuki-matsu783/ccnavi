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

from tests.ticket.test_phases import PhaseHarness
from tests.ticketsample import child_text, parent_text


class ApprovalToldTest(PhaseHarness):
    # ---- 道具

    def event(self, event, session="s1", agent_id="", tool="", **tool_input):
        payload = {"hook_event_name": event, "cwd": self.parent_tree, "session_id": session}
        if tool:
            payload["tool_name"] = tool
            payload["tool_input"] = tool_input
        if agent_id:
            payload["agent_id"] = agent_id
        result = self.ccnavi("--mode", "enable", stdin=json.dumps(payload))
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

    def approve_body(self, tickets):
        # ボードと同じく、見せたダイジェスト（承認画面の本文・判定が読んだ中身・書き込む中身）
        # を渡す。
        # 渡さない `--yes` は承認しない。
        shown = self.ccnavi("--agree", "--preview", "--json")
        digest = json.loads(shown.stdout)["digest"]
        result = self.ccnavi(
            "user", "agree", "--yes", ",".join(tickets), "--digest", digest, "--json"
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def approve_yes(self, tickets):
        return self.approve_body(tickets)["prompt"]

    def leave_change_in_parent(self, name="keep.py"):
        """承認の前に、親のワークツリーへ提案以外の変更を入れる（追跡中のファイルの書き換え）。"""
        with open(os.path.join(self.parent_tree, "src", name), "a", encoding="utf-8") as f:
            f.write("print(2)\n")

    def parent_only(self):
        self.propose("i0001", parent_text("i0001", ["research", "design"]))
        self.commit_parent()

    def next_child(self, name="i0001-01-01"):
        self.propose(name, child_text(name, "i0001", 1, ("wip/research/*",), False))
        self.commit_parent()

    # ---- 1. 拡張が渡す文

    def test_the_prompt_names_the_approved_tickets_and_the_order_of_start(self):
        self.parent_only()
        self.next_child()
        prompt = self.approve_yes(["i0001", "i0001-01-01"])
        self.assertIn("i0001-01-01", prompt)
        # 子より先に親を着手する順も、この文で伝える。
        self.assertIn("start <親>", prompt)

    def test_a_batch_without_a_new_parent_does_not_ask_for_the_parent_start(self):
        """子だけの回では、親の `start` を勧めないこと。

        勧めたコマンドは、親が着手済みなら「着手済み」で終わる。案内どおりに打って終了コード 1 を
        受け取る文は、案内ではなく誤りの元になる。子より先に親を着手する順そのものは、
        子の行の「親が未着手だと止まります）。」で残る。
        """
        self.parent_only()
        self.next_child()
        self.approve_yes(["i0001", "i0001-01-01"])
        self.next_child("i0001-01-02")
        told = self.approve_yes(["i0001-01-02"])
        self.assertNotIn("start <親>", told)
        self.assertIn("親が未着手だと止まります）。", told)

    def test_a_revision_is_told_by_the_prompt(self):
        self.parent_only()
        self.approve_yes(["i0001"])
        self.propose("i0001", parent_text("i0001", ["research", "design", "acceptance"]))
        self.commit_parent()
        self.assertIn("改版", self.approve_yes(["i0001"]))

    # ---- 1b. 親のワークツリーに残る、提案以外の変更

    def test_the_prompt_tells_about_a_change_left_in_the_parent_worktree(self):
        """承認の前に親のワークツリーへ入れた変更は、承認の時点で知らせる（承認は止めない）。

        承認の後は、同じ変更を戻す編集が DENY_TICKET_PARENT で断られ、子の finish も止まる。
        戻し方（restore）まで `案内:` の行に載せる。
        """
        self.parent_only()
        self.leave_change_in_parent()
        body = self.approve_body(["i0001"])
        self.assertEqual(body["approved"], ["i0001"])
        notices = [x for x in body["prompt"].splitlines() if x.startswith("案内:")]
        self.assertEqual(len(notices), 1, body["prompt"])
        self.assertIn("src/keep.py", notices[0])
        self.assertIn("ccnavi-git.sh", notices[0])
        self.assertIn("restore -- src/keep.py", notices[0])
        self.assertIn("子のワークツリー", notices[0])
        self.assertEqual(body["dirty_parent"], [{"ticket": "i0001", "paths": ["src/keep.py"]}])

    def test_the_prompt_is_silent_when_the_parent_worktree_is_clean(self):
        """親のワークツリーが提案とチケットの置き場しか変えていなければ、知らせない。"""
        self.parent_only()
        body = self.approve_body(["i0001"])
        self.assertNotIn("案内:", body["prompt"])
        self.assertEqual(body["dirty_parent"], [])

    def test_a_child_approval_names_the_parent_worktree(self):
        """子の承認でも、変更が残っているのは親のワークツリー。親の識別子で名指しする。"""
        self.parent_only()
        self.approve_yes(["i0001"])
        self.commit_parent("approve parent")
        self.next_child()
        self.leave_change_in_parent()
        body = self.approve_body(["i0001-01-01"])
        self.assertEqual(body["dirty_parent"], [{"ticket": "i0001", "paths": ["src/keep.py"]}])

    def test_terminal_agree_reports_a_change_left_in_the_parent_worktree(self):
        """ターミナルの承認は、標準エラーと `--placed-out` の JSON の両方に出す。承認は通る。"""
        self.parent_only()
        self.leave_change_in_parent()
        report = os.path.join(self.root, "placed.json")
        result = self.ccnavi("user", "agree", "--placed-out", report, stdin="y\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("src/keep.py", result.stderr)
        self.assertIn("restore -- src/keep.py", result.stderr)
        with open(report, encoding="utf-8") as f:
            body = json.load(f)
        self.assertEqual(body["dirty_parent"], [{"ticket": "i0001", "paths": ["src/keep.py"]}])
        self.assertEqual(len(body["placed"]), 1)

    def test_a_missing_parent_worktree_does_not_stop_the_approval(self):
        """親のワークツリーが無いときは黙って空にする。承認は止めない。"""
        from types import SimpleNamespace

        from ccnavi.infra import settings
        from ccnavi.tickets import core_approve

        self.parent_only()
        conf, _ = settings.load(self.root)
        ticket = SimpleNamespace(ticket="i9999", parent="", is_child=False, project="")
        self.assertEqual(core_approve.dirty_parents(conf, self.root, [ticket]), [])

    def test_the_parent_denial_tells_how_to_put_a_change_back(self):
        """承認の後、親のワークツリーの変更を Edit で戻そうとして断られたとき、戻し方を案内する。"""
        self.parent_only()
        self.leave_change_in_parent()
        self.approve_yes(["i0001"])
        result = self.event(
            "PreToolUse",
            tool="Edit",
            file_path=os.path.join(self.parent_tree, "src", "keep.py"),
            old_string="print(2)",
            new_string="",
        )
        out = json.loads(result.stdout)["hookSpecificOutput"]
        reason = out.get("permissionDecisionReason", "")
        self.assertEqual(out.get("permissionDecision"), "deny")
        self.assertIn("DENY_TICKET_PARENT", reason)
        self.assertIn("ccnavi-git.sh", reason)
        self.assertIn("restore -- <path>", reason)
        self.assertIn("do not work around", reason)

    # ---- 2. hook は伝えない

    def test_hooks_do_not_tell_about_approvals(self):
        self.parent_only()
        self.assertEqual(self.prompt(), "")
        self.before()
        self.context(self.event("SessionStart"))
        self.next_child()
        self.approve_yes(["i0001", "i0001-01-01"])
        self.assertNotIn("承認され", self.prompt())
        self.assertNotIn("承認され", self.before())
        self.assertNotIn("承認され", self.before(session="s2", agent_id="sub-1"))
        # セッション開始の一覧（このPCで作業中の親）は次の子を名指しする。承認の知らせは言わない
        self.assertNotIn("承認され", self.context(self.event("SessionStart")))
        self.assertNotIn("承認され", self.prompt(session="s2"))

    def test_one_hook_reads_the_approved_copies_once(self):
        """UserPromptSubmit と PreToolUse（ワークツリーへの書き込み）は、置き場を 1 度だけ読む。

        承認の知らせは外したが、範囲（実行後の側・実行前の判定）は同じ読みを使い回す。
        """
        from unittest import mock

        from ccnavi.tickets import approval

        self.parent_only()
        self.next_child()
        self.approve_yes(["i0001", "i0001-01-01"])
        with mock.patch.object(approval, "read_raw", wraps=approval.read_raw) as read_raw:
            self.assertNotIn("承認され", self.prompt())
        self.assertEqual(read_raw.call_count, 1)
        with mock.patch.object(approval, "read_raw", wraps=approval.read_raw) as read_raw:
            self.assertNotIn("承認され", self.before())
        self.assertEqual(read_raw.call_count, 1)

    def test_the_verdict_still_comes_back_after_an_approval(self):
        self.parent_only()
        self.next_child()
        self.approve_yes(["i0001", "i0001-01-01"])
        result = self.event(
            "PreToolUse", tool="Write", file_path=os.path.join(self.parent_tree, "src", "x.py")
        )
        # 親のワークツリーは置き場の外に書けない。判定は承認の後も同じ形で返る。
        out = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(out.get("permissionDecision"), "deny")
        self.assertIn("DENY_TICKET_PARENT", out.get("permissionDecisionReason", ""))

    # ---- 3. 記録を書かない

    def test_no_memo_of_told_approvals_is_written(self):
        self.parent_only()
        self.event("SessionStart")
        self.prompt()
        self.before()
        self.next_child()
        self.approve_yes(["i0001", "i0001-01-01"])
        self.prompt()
        self.before()
        self.assertEqual(glob.glob(os.path.join(self.state, "approved-*")), [])


if __name__ == "__main__":
    unittest.main()
