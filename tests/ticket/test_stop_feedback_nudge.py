"""フィードバック計画待ちのまま終えようとしたときの促し（NUDGE_TICKET_FEEDBACK）の受入テスト。

全体計画のレビューが済んだ親は、閉じる前にフィードバック計画が要る。局面が「フィードバック計画待ち」の
親のツリーで終えようとしたとき、1 回だけ止めて次の一手を返す。

1. 初回は促し、同じ状態での 2 回目は促さない
2. 実行中のサブエージェントがいる間は促さず、「1 回だけ」を使い切らない
3. フィードバック計画が承認済みなら促さない
4. 親が blocked なら促さない
5. 改版を提案済みなら、提案済みの文面になる。未提案から提案済みに変わったら、もう 1 回だけ促す
6. 連鎖の 2 回目（stop_hook_active）・サブエージェントの中・ワークスペースルートでは促さない
"""

from __future__ import annotations

import json
import os
import unittest

from ccnavi.hook import subagent
from ccnavi.infra import settings
from tests.ticket.test_feedback_plan_approval import FeedbackPlanApprovalTest
from tests.ticket.test_ticket import write

CODE = "NUDGE_TICKET_FEEDBACK"


def load_tests(loader, tests, pattern):
    """このモジュールで書いたテストだけを走らせる（借りたテストは走らせない）。"""
    suite = unittest.TestSuite()
    for name in sorted(vars(FeedbackNudgeTest)):
        if name.startswith("test_"):
            suite.addTest(FeedbackNudgeTest(name))
    return suite


class FeedbackNudgeTest(FeedbackPlanApprovalTest):
    def waiting(self):
        """全体計画のレビューが済み、フィードバック計画が決まっていない局面まで。"""
        fixture = self.requested()
        self.assertEqual(self.confirm(fixture, 1).returncode, 0)
        self.commit_parent("reviewed")

    def stop(self, cwd=None, *, active=False, agent_id="", session="s1"):
        payload = {
            "hook_event_name": "Stop",
            "cwd": cwd or self.parent_tree,
            "session_id": session,
            "stop_hook_active": active,
        }
        if agent_id:
            payload["agent_id"] = agent_id
        result = self.ccnavi("--mode", "enable", stdin=json.dumps(payload))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def asked(self, body):
        self.assertEqual(body.get("decision"), "block", body)
        self.assertIn(f"[ccnavi] {CODE} (parent: i0001)", body["reason"])
        return body["reason"]

    def quiet(self, body):
        self.assertNotIn(CODE, json.dumps(body, ensure_ascii=False))

    def todo(self, name="i0001"):
        return os.path.join(self.parent_tree, "wip", "proposals", "todo", name + ".md")

    def test_it_asks_once_and_names_the_todo_folder(self):
        self.waiting()
        reason = self.asked(self.stop())
        todo_dir = os.path.dirname(self.todo())
        self.assertIn(todo_dir, reason)
        self.assertIn("feedback: []", reason)
        # 改版の雛形は手で写さず、コマンドが作る（スクリプトだけが書く欄の始末も済ませる）
        launcher = settings.script_command(self.root, "ccnavi-launcher.sh")
        self.assertIn(f"{launcher} ticket revise i0001 --feedback", reason)
        self.assertIn("approval-request i0001 --check", reason)
        ticket_sh = settings.script_command(self.root, "ccnavi-ticket.sh")
        self.assertIn(f"`{ticket_sh} approval-request i0001`", reason)
        # ユーザが止めたときの扱いと、同じ状態では 1 回だけであること
        self.assertIn("止めて", reason)
        self.assertIn("同じ状態では 1 回だけ", reason)
        self.quiet(self.stop())

    def test_the_chain_the_subagent_and_the_root_are_not_asked(self):
        self.waiting()
        self.quiet(self.stop(active=True))
        self.quiet(self.stop(agent_id="a1"))
        self.quiet(self.stop(self.root))
        # どれも「1 回だけ」を使い切っていない
        self.asked(self.stop())

    def test_a_running_subagent_holds_it_without_using_it_up(self):
        self.waiting()
        started = self.hook("SubagentStart", "", self.parent_tree, agent_id="a1")
        self.assertEqual(started.returncode, 0, started.stderr)
        self.assertTrue(os.path.exists(subagent._running_path(self.state, "s1", "a1")))
        self.quiet(self.stop())
        self.quiet(self.stop())
        self.assertEqual(
            self.hook("SubagentStop", "", self.parent_tree, agent_id="a1").returncode, 0
        )
        self.asked(self.stop())

    def test_an_approved_feedback_plan_is_not_asked(self):
        self.waiting()
        self.propose_feedback(feedback=[])
        self.assertEqual(self.approve().returncode, 0)
        self.quiet(self.stop())

    def test_a_blocked_parent_is_not_asked(self):
        self.waiting()
        copy = os.path.join(self.approved, "doing", "i0001.md")
        with open(copy, encoding="utf-8") as f:
            text = f.read()
        # `workflow:` の欄を持つ承認済みチケットは、判定が信頼しない（blocked）
        write(copy, text.replace("version: 1\n", "version: 1\nworkflow: x\n", 1))
        self.quiet(self.stop())

    def test_a_proposed_revision_changes_the_text_and_asks_once_more(self):
        self.waiting()
        self.asked(self.stop())
        self.quiet(self.stop())
        # 改版を書く（feedback: [] を持つ親と同じ識別子の提案）
        self.propose_feedback(feedback=[])
        reason = self.asked(self.stop())
        self.assertIn(f"フィードバック計画の改版は {self.todo()} にあります", reason)
        ticket_sh = settings.script_command(self.root, "ccnavi-ticket.sh")
        self.assertIn(f"`{ticket_sh} approval-request i0001`", reason)
        self.assertIn(f"`{ticket_sh} status i0001`", reason)
        self.assertNotIn("started_at・completed_at", reason)
        self.quiet(self.stop())

    def test_a_proposal_without_feedback_is_not_a_proposed_revision(self):
        self.waiting()
        from tests.ticketsample import parent_text

        self.start_parent()
        self.propose("i0001", parent_text("i0001", ["design"]))
        reason = self.asked(self.stop())
        self.assertIn("全体計画のレビューが済み", reason)
        self.assertNotIn("の改版は", reason)


if __name__ == "__main__":
    unittest.main()
