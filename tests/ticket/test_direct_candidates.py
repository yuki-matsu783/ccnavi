"""直接作業の候補の記録と、それを使う Stop の促しの受入テスト。

登録は成功した Write・Edit・NotebookEdit の書き込み先で、git を呼ばない。確かめるのは Stop で、
cwd のツリーのあとに、記録の候補を `checked` の古い順に調べる。外すのは「ヒットしない」と確認できた
ものだけで、期限不足・git の失敗などの「不明」は外さない（期限と「不明」、記録の上限は go の
directcand・integrate のテストが見る）。
道具は IntegrateNudgeTest のものを借りる（借りたテストは走らせない）。
"""

from __future__ import annotations

import json
import os
import unittest

from tests.ticket import test_stop_integrate_nudge as base
from tests.ticket.test_ticket import git, write


def record_path(state: str, session: str) -> str:
    """セッションごとの候補の記録（`<state>/direct-<セッション>.json`）。"""
    return os.path.join(state, f"direct-{session}.json")


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for name in sorted(vars(DirectCandidatesTest)):
        if name.startswith("test_"):
            suite.addTest(DirectCandidatesTest(name))
    return suite


class DirectCandidatesTest(base.IntegrateNudgeTest):
    def post(self, path, *, tool="Write", session="s1", agent_id="", cwd=None, rel=False):
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": tool,
            "tool_input": {"file_path": path},
            "tool_response": {},
            "session_id": session,
            "cwd": cwd or self.root,
        }
        if tool == "NotebookEdit":
            payload["tool_input"] = {"notebook_path": path}
        if agent_id:
            payload["agent_id"] = agent_id
        return self.ccnavi("--mode", "enable", stdin=json.dumps(payload))

    def entries(self, session="s1"):
        path = record_path(self.state, session)
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return json.load(f)["entries"]

    # ---- 登録

    def test_reads_and_the_main_tree_are_not_registered(self):
        path = self.direct(commit=True)
        for tool in ("Read", "Grep", "Bash"):
            self.post(os.path.join(path, "src", "a.py"), tool=tool)
        self.post(os.path.join(self.root, "src", "main.py"))
        self.assertEqual(self.entries(), [])
        self.post(os.path.join(path, "src", "a.py"), tool="Edit")
        self.assertEqual([e["name"] for e in self.entries()], ["scratch"])

    def test_a_relative_path_is_resolved_from_cwd(self):
        path = self.direct(commit=True)
        self.post("src/x.py", cwd=path)
        self.assertEqual([e["name"] for e in self.entries()], ["scratch"])

    def test_a_recent_tail_entry_is_not_rewritten(self):
        path = self.direct(commit=True)
        self.post(os.path.join(path, "a.py"))
        file = record_path(self.state, "s1")
        first = os.stat(file).st_mtime_ns
        self.post(os.path.join(path, "b.py"))
        self.assertEqual(os.stat(file).st_mtime_ns, first)

    # ---- Stop

    def test_the_root_cwd_is_asked_through_a_candidate(self):
        path = self.direct(commit=True)
        self.post(os.path.join(path, "src", "a.py"))
        reason = self.asked(self.stop(self.root))
        self.assertIn("worktree: scratch", reason)
        # 同じ状態では 1 回だけ。
        self.assert_not_asked(self.stop(self.root))

    def test_the_nudge_commands_run_from_a_fresh_shell_at_the_root(self):
        import re
        import subprocess

        path = self.direct(commit=True)
        write(os.path.join(path, "src", "draft.py"), "y\n")
        reason = self.asked(self.stop(path))
        commands = re.findall(r"`([^`]+)`", reason)
        self.assertTrue(any(" --adopt " in c for c in commands), commands)
        for command in commands:
            if " integrate" in command or " add " in command or " commit " in command:
                continue
            # テストのワークスペースにはスクリプトが無いので、実行はせず、新しいシェルで 1 行として
            # 読めること（引用が閉じていること）と、`cd` の行き先が実在することを確かめる。
            done = subprocess.run(
                ["sh", "-n", "-c", command], cwd="/", capture_output=True, text=True
            )
            self.assertEqual(done.returncode, 0, (command, done.stderr))
            target = command.split(" && ")[0][len("cd ") :].strip("\"'")
            self.assertTrue(os.path.isdir(target), command)
        # 打てる形になっているか（`<…>` 以外に未解決の差し込みが無い）。
        for command in commands:
            self.assertNotIn("{", command)
            self.assertTrue(command.startswith("cd "), command)

    def test_ticket_control_does_not_matter(self):
        path = self.direct(commit=True)
        self.post(os.path.join(path, "src", "a.py"))
        payload = json.dumps({"hook_event_name": "Stop", "cwd": self.root, "session_id": "s1"})
        self.point_origin_head("main")
        off = self.ccnavi(
            "--mode",
            "enable",
            "--ticket-control",
            "disable",
            stdin=payload,
        )
        self.asked(off)

    def test_a_subagent_write_is_asked_after_it_finishes(self):
        path = self.direct(commit=True)
        start = {
            "hook_event_name": "SubagentStart",
            "cwd": self.root,
            "session_id": "s1",
            "agent_id": "a1",
            "agent_type": "general-purpose",
        }
        self.ccnavi("--mode", "enable", stdin=json.dumps(start))
        self.post(os.path.join(path, "src", "a.py"), agent_id="a1")
        self.assert_not_asked(self.stop(self.root))
        stop = dict(start, hook_event_name="SubagentStop")
        self.ccnavi("--mode", "enable", stdin=json.dumps(stop))
        self.asked(self.stop(self.root, session="s1"))

    def test_a_running_subagent_defers_the_nudge_without_ticket_control_too(self):
        path = self.direct(commit=True)
        off = ("--ticket-control", "disable")
        start = {
            "hook_event_name": "SubagentStart",
            "cwd": path,
            "session_id": "s1",
            "agent_id": "a1",
            "agent_type": "general-purpose",
        }
        self.ccnavi("--mode", "enable", *off, stdin=json.dumps(start))
        payload = json.dumps({"hook_event_name": "Stop", "cwd": path, "session_id": "s1"})
        self.point_origin_head("main")
        self.assert_not_asked(self.ccnavi("--mode", "enable", *off, stdin=payload))
        stop = dict(start, hook_event_name="SubagentStop")
        self.ccnavi("--mode", "enable", *off, stdin=json.dumps(stop))
        self.asked(self.ccnavi("--mode", "enable", *off, stdin=payload))

    def test_another_sessions_record_is_not_used(self):
        path = self.direct(commit=True)
        self.post(os.path.join(path, "src", "a.py"), session="other")
        self.assert_not_asked(self.stop(self.root, session="s1"))

    def test_a_commit_made_by_bash_after_the_last_edit_is_still_asked(self):
        path = self.direct()
        self.post(os.path.join(path, "src", "a.py"))
        self.assert_not_asked(self.stop(self.root, session="s1"))
        self.commit_work(path, rel="src/late.py")
        self.asked(self.stop(self.root, session="s1"))

    def test_an_already_nudged_cwd_lets_another_candidate_be_asked(self):
        first = self.direct("scratch", commit=True)
        second = self.direct("other", commit=True)
        self.post(os.path.join(second, "src", "a.py"))
        self.asked(self.stop(first))
        reason = self.asked_name(self.stop(first), "other")
        self.assertIn("other", reason)

    def asked_name(self, result, name):
        body = self.body(result)
        self.assertEqual(body.get("decision"), "block", result.stdout)
        self.assertIn(f"(worktree: {name})", body["reason"])
        return body["reason"]

    def test_a_missing_directory_is_dropped_from_the_record(self):
        path = self.direct(commit=True)
        self.post(os.path.join(path, "src", "a.py"))
        git(self.root, "worktree", "remove", "--force", path)
        self.assert_not_asked(self.stop(self.root))
        self.assertEqual(self.entries(), [])

    # ---- 期限と「不明」


if __name__ == "__main__":
    unittest.main()
