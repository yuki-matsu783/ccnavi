"""hook が読めない payload を受けたときの受入テスト。

終了コード 1 で終わると、Claude Code は PreToolUse の呼び出しをそのまま通す。入口で受けて、
イベントごとに決めた形で終えることを確かめる。

ccnavi 自身の失敗（内部エラー）の受け皿と、判定に達しなかった回をツールの種類で通すか止めるかは、
外から失敗を起こせないので Go のテストが確かめる（go/internal/entry/cli の internal_error_test.go と
go/internal/hook/failsafe）。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from tests import ROOT
from tests.inproc import run_ccnavi


def run_raw(root, text, mode="enable"):
    environment = {
        **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
        "CCNAVI_LEARNING": "disable",
    }
    environment["CCNAVI_GUARD_CORE_FILES"] = "disable"
    environment.pop("CLAUDE_PROJECT_DIR", None)
    return run_ccnavi(["--root", root, "--mode", mode], input=text, cwd=ROOT, env=environment)


def rows(root):
    log = os.path.join(root, "logs", "decisions.jsonl")
    if not os.path.exists(log):
        return []
    with open(log, encoding="utf-8") as f:
        return [json.loads(line) for line in f.read().splitlines()]


PRE = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "echo hi"}}


class UnusablePayloadTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = directory.name

    def test_lone_surrogate_in_the_command_is_judged_and_recorded(self):
        """JSON の "\\ud800"（UTF-8 にできない字）がコマンドに入っても、判定は効き、記録も残る。

        記録には UTF-8 にできない字の代わりに U+FFFD が残る。
        """
        text = (
            '{"hook_event_name":"PreToolUse","session_id":"s1","tool_name":"Bash",'
            '"tool_input":{"command":"rm -rf /tmp/x \\ud800"}}'
        )
        result = run_raw(self.root, text)
        self.assertEqual(result.returncode, 0, result.stderr)
        verdict = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(verdict["permissionDecision"], "deny")
        row = rows(self.root)[-1]
        self.assertEqual(row["decision"], "deny")
        self.assertIn("rm -rf /tmp/x \ufffd", row["subject"])

    def test_event_name_of_the_wrong_type_is_an_unusable_payload(self):
        """`hook_event_name` が文字列でない payload は、読めない payload として止めて記録する。"""
        for payload in (
            {**PRE, "hook_event_name": ["PreToolUse"]},
            {**PRE, "tool_name": ["Bash"]},
        ):
            with self.subTest(payload=payload):
                result = run_raw(self.root, json.dumps(payload))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("[ccnavi] payload-unusable", result.stderr)
                row = rows(self.root)[-1]
                self.assertEqual(row["reason"], "payload-unusable")
                self.assertEqual(row["decision"], "skip")

    def test_deeply_nested_claude_settings_are_skipped(self):
        """入れ子の深すぎる settings.local.json は読めないファイルとして飛ばし、判定は続ける。"""
        path = os.path.join(self.root, ".claude", "settings.local.json")
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as f:
            f.write("[" * 200000)
        rm = {**PRE, "tool_input": {"command": "rm -rf /tmp/x"}}
        result = run_raw(self.root, json.dumps(rm))
        self.assertEqual(result.returncode, 0, result.stderr)
        verdict = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(verdict["permissionDecision"], "deny")

    def test_tool_input_of_the_wrong_type_is_an_unusable_payload(self):
        """tool_input がオブジェクトでない・読む欄が文字列でない payload は、空として通さない。"""
        for tool_input in (["git", "push"], {"command": ["git", "push"]}, {"file_path": 1}):
            payload = {**PRE, "tool_input": tool_input}
            with self.subTest(tool_input=tool_input):
                result = run_raw(self.root, json.dumps(payload))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("[ccnavi] payload-unusable", result.stderr)
                self.assertEqual(rows(self.root)[-1]["reason"], "payload-unusable")

    def test_unusable_payload_says_what_to_do(self):
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        result = run_ccnavi(
            ["--root", self.root, "--log", "", "--mode", "enable"],
            input="not json",
            cwd=ROOT,
            env=environment,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("[ccnavi] payload-unusable", result.stderr)
        # イベント名すら読めない回は、何を止めたかを言わず、直す側だけを名指しする。
        self.assertIn("exit code 2 で止まりました", result.stderr)
        self.assertNotIn("この呼び出し", result.stderr)

    def test_unusable_payload_text_follows_the_event(self):
        """exit 2 が何を止め、文が誰に届くかはイベントで違う。文をそれに合わせる。"""
        cases = {
            "PreToolUse": ("判定できないので止めました", "確かめてもらい"),
            "PostToolUse": ("すでに実行済み", "確かめてもらい"),
            "UserPromptSubmit": ("Claude に渡す前に止めました", "送り直しても通らない"),
            "Stop": ("終わらせませんでした", "ターンを終える"),
            "SubagentStop": ("終わらせませんでした", "最終報告"),
        }
        for event, (what, then) in cases.items():
            with self.subTest(event=event):
                payload = {"hook_event_name": event, "session_id": 1}
                result = run_raw(self.root, json.dumps(payload))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(what, result.stderr)
                self.assertIn(then, result.stderr)
                if event != "PreToolUse":
                    self.assertNotIn("この呼び出しを判定できない", result.stderr)
                row = rows(self.root)[-1]
                self.assertEqual(row["reason"], "payload-unusable")
                self.assertEqual(row["event"], event)

    def test_unusable_payload_in_dry_run_does_not_say_stopped(self):
        """ツールの呼び出しを持たないイベントは、dry-run なら exit 0 で通す。

        「止めた」とは言わず、enable なら止めていたと言う。
        """
        for event in ("UserPromptSubmit", "Stop"):
            with self.subTest(event=event):
                payload = {"hook_event_name": event, "cwd": 1}
                result = run_raw(self.root, json.dumps(payload), mode="dry-run")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("[ccnavi] payload-unusable", result.stderr)
                self.assertIn("何も止めていません", result.stderr)
                self.assertNotIn("止めました", result.stderr)

    def test_unusable_payload_before_a_tool_call_stops_even_in_dry_run(self):
        """ツールが分からない呼び出しの前は、dry-run でも止める。"""
        for event in ("PreToolUse", ""):
            with self.subTest(event=event):
                payload = {"hook_event_name": event, "cwd": 1}
                result = run_raw(self.root, json.dumps(payload), mode="dry-run")
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("[ccnavi] payload-unusable", result.stderr)
                self.assertNotIn("何も止めていません", result.stderr)


if __name__ == "__main__":
    unittest.main()
