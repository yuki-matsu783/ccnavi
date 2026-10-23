"""hook の処理が ccnavi 自身の例外で終わったときの受入テスト。

例外を抜けさせると Python は終了コード 1 で終わり、Claude Code は PreToolUse の呼び出しを
そのまま通す。入口で受けて、イベントごとに決めた形で終えることを確かめる。

例外は、イベントの処理の実体（`judge.decide_before`・`events.decide_after`・
`events.decide_at_stop`）を差し替えて起こす。ファサード（`core`・`diagnose`）には触れない。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from ccnavi.entry import cli_args
from ccnavi.hook import events, judge
from ccnavi.infra import hookio, modes, settings
from ccnavi.policy import selfguard
from tests import ROOT
from tests.inproc import run_ccnavi


def run(root, payload, mode="enable"):
    result = run_raw(root, json.dumps(payload), mode)
    return result, rows(root)[-1]


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


def boom(*_args, **_kwargs):
    raise RuntimeError("想定外")


PRE = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "echo hi"}}


class InternalErrorTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = directory.name

    def test_pre_tool_use_stops_and_records(self):
        with mock.patch.object(judge, "decide_before", side_effect=boom):
            result, row = run(self.root, PRE)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("[ccnavi] internal-error", result.stderr)
        self.assertIn("RuntimeError", result.stderr)
        self.assertIn(os.path.join("logs", "diag", "ccnavi.log"), result.stderr)
        self.assertIn("Tell the user", result.stderr)
        # 例外の文はどこにも出さない。
        self.assertNotIn("想定外", result.stderr)
        self.assertEqual(row["decision"], "skip")
        self.assertEqual(row["reason"], "internal-error")
        self.assertIn("RuntimeError", row["detail"])
        with open(os.path.join(self.root, "logs", "diag", "ccnavi.log"), encoding="utf-8") as f:
            diag = f.read()
        self.assertIn("ERROR", diag)
        self.assertIn("error=RuntimeError", diag)

    def test_pre_tool_use_in_dry_run_lets_only_a_safe_read_through(self):
        # 判定できない間に通すのは、狭い許可リストの読み取りだけ。dry-run でも変わらない。
        safe = {**PRE, "tool_input": {"command": "ls"}}
        with mock.patch.object(judge, "decide_before", side_effect=boom):
            result, row = run(self.root, safe, mode="dry-run")
            stopped, _ = run(self.root, PRE, mode="dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stopped.returncode, 2, stopped.stderr)
        self.assertEqual(row["reason"], "internal-error")

    def test_output_written_before_the_error_is_dropped(self):
        # 途中まで書いた JSON が残ると、内部エラーの応答と混ざって読まれない。
        def half(stdout, *_args, **_kwargs):
            stdout.write('{"hookSpecificOutput": ')
            raise RuntimeError("想定外")

        with mock.patch.object(events, "decide_at_stop", side_effect=half):
            result, _ = run(self.root, {"hook_event_name": "Stop"})
        self.assertIn("systemMessage", json.loads(result.stdout))

    def test_post_tool_use_tells_the_model(self):
        payload = {**PRE, "hook_event_name": "PostToolUse"}
        with mock.patch.object(events, "decide_after", side_effect=boom):
            result, row = run(self.root, payload)
        self.assertEqual(result.returncode, 2)
        self.assertIn("were not checked", result.stderr)
        self.assertEqual(row["reason"], "internal-error")

    def test_other_events_do_not_stop_the_work(self):
        # Stop を 2 で終えると終われなくなる。ユーザには systemMessage で知らせる。
        with mock.patch.object(events, "decide_at_stop", side_effect=boom):
            result, row = run(self.root, {"hook_event_name": "Stop"})
        self.assertEqual(result.returncode, 0, result.stderr)
        said = json.loads(result.stdout)["systemMessage"]
        self.assertIn("internal-error", said)
        self.assertIn("Nothing was stopped", said)
        self.assertEqual(row["reason"], "internal-error")

    def test_lone_surrogate_in_the_command_is_judged_and_recorded(self):
        """JSON の "\\ud800"（UTF-8 にできない字）がコマンドに入っても、判定は効き、記録も残る。"""
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
        self.assertIn("\ud800", row["subject"])

    def test_failure_while_recording_stops_the_call(self):
        """判定の後の記録で落ちても、終了コード 1 で通さない。記録は重ねて試さない。"""
        calls = []

        def broken(*_args, **_kwargs):
            calls.append(1)
            raise ValueError("想定外")

        with mock.patch.object(cli_args, "_record", side_effect=broken):
            result = run_raw(self.root, json.dumps(PRE))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("[ccnavi] internal-error", result.stderr)
        self.assertIn("ValueError", result.stderr)
        self.assertEqual(len(calls), 1)

    def test_failure_before_the_payload_is_read_stops_the_call(self):
        """payload を読む前に落ちたら、イベントが分からないので止めて記録する。"""
        with mock.patch.object(modes, "resolve_mode", side_effect=RuntimeError("想定外")):
            result = run_raw(self.root, json.dumps(PRE))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("[ccnavi] internal-error", result.stderr)
        row = rows(self.root)[-1]
        self.assertEqual(row["reason"], "internal-error")
        self.assertIn("RuntimeError", row["detail"])

    def test_failure_after_the_payload_is_read_follows_the_event(self):
        """payload を読んだ後なら、イベントごとに決めた形で終える。Stop は止めない。"""
        with mock.patch.object(judge, "subject_of", side_effect=RuntimeError("想定外")):
            result, row = run(self.root, {"hook_event_name": "Stop"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("internal-error", json.loads(result.stdout)["systemMessage"])
        self.assertEqual(row["reason"], "internal-error")
        self.assertEqual(row["event"], "Stop")

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

    def test_failure_while_loading_settings_stops_the_call(self):
        """hook の経路で設定の読み込みが落ちても、終了コード 1 で通さない。読む前なので止める。"""
        with mock.patch.object(settings, "load", side_effect=RecursionError("深い")):
            result = run_raw(self.root, json.dumps(PRE))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("[ccnavi] internal-error", result.stderr)
        self.assertEqual(rows(self.root)[-1]["reason"], "internal-error")

    def test_failure_while_loading_settings_outside_the_hook_is_raised(self):
        """hook でない経路（打った人が見られる経路）は、例外のまま伝える。"""
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        with (
            mock.patch.object(settings, "load", side_effect=RecursionError("深い")),
            self.assertRaises(RecursionError),
        ):
            run_ccnavi(["--root", self.root, "--explain"], cwd=ROOT, env=environment)

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

    def test_every_subject_field_is_checked_for_its_type(self):
        """判定が文字列として読む欄は、どれも型を確かめる欄の一覧に入っている。"""
        missing = set(judge.SUBJECT_FIELDS.values()) - set(hookio.TOOL_INPUT_TEXT_FIELDS)
        self.assertEqual(missing, set())

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
        self.assertIn("The user needs to check", result.stderr)
        self.assertNotIn("this call", result.stderr)

    def test_unusable_payload_text_follows_the_event(self):
        """exit 2 が何を止め、文が誰に届くかはイベントで違う。文をそれに合わせる。"""
        cases = {
            "PreToolUse": ("stopped it", "Tell the user"),
            "PostToolUse": ("has already run", "Tell the user"),
            "UserPromptSubmit": ("held this message back", "Sending it again"),
            "Stop": ("kept it from ending", "end the turn"),
            "SubagentStop": ("kept it from ending", "final report"),
        }
        for event, (what, then) in cases.items():
            with self.subTest(event=event):
                payload = {"hook_event_name": event, "session_id": 1}
                result = run_raw(self.root, json.dumps(payload))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(what, result.stderr)
                self.assertIn(then, result.stderr)
                if event != "PreToolUse":
                    self.assertNotIn("judge this call", result.stderr)
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
                self.assertIn("nothing was stopped", result.stderr)
                self.assertNotIn("stopped it", result.stderr)
                self.assertNotIn("held this message back", result.stderr)

    def test_unusable_payload_before_a_tool_call_stops_even_in_dry_run(self):
        """ツールが分からない呼び出しの前は、dry-run でも止める。"""
        for event in ("PreToolUse", ""):
            with self.subTest(event=event):
                payload = {"hook_event_name": event, "cwd": 1}
                result = run_raw(self.root, json.dumps(payload), mode="dry-run")
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("[ccnavi] payload-unusable", result.stderr)
                self.assertNotIn("nothing was stopped", result.stderr)


class FailClosedByToolTest(unittest.TestCase):
    """判定に達せなかった回の結論は、ツールの種類で決まる。"""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = directory.name

    def crash(self, tool, tool_input, mode="enable"):
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "tool_input": tool_input,
            "cwd": self.root,
        }
        with mock.patch.object(judge, "decide_before", side_effect=boom):
            result, _ = run(self.root, payload, mode)
        return result.returncode

    def test_writes_and_agents_stop_even_in_dry_run(self):
        for tool, tool_input in (
            ("Write", {"file_path": "/tmp/x"}),
            ("Edit", {"file_path": "/tmp/x"}),
            ("NotebookEdit", {"notebook_path": "/tmp/x.ipynb"}),
            ("Agent", {"description": "d"}),
            ("Task", {"description": "d"}),
        ):
            with self.subTest(tool=tool, mode="dry-run"):
                self.assertEqual(self.crash(tool, tool_input, "dry-run"), 2)

    def test_reads_pass_unless_the_minimal_deny_hits(self):
        for mode in ("enable", "dry-run"):
            with self.subTest(mode=mode):
                self.assertEqual(self.crash("Read", {"file_path": "/tmp/notes.md"}, mode), 0)
                self.assertEqual(self.crash("Read", {"file_path": "/home/u/.env"}, mode), 2)
                self.assertEqual(self.crash("Read", {"file_path": "/r/.git/config"}, mode), 2)
                self.assertEqual(
                    self.crash("Read", {"file_path": "/r/logs/state/ops/a.json"}, mode), 2
                )
                self.assertEqual(self.crash("Grep", {"path": "/home/u/.ssh/id_rsa"}, mode), 2)

    def test_shell_passes_only_the_narrow_allowlist(self):
        for command, expected in (
            ("pwd", 0),
            ("ls", 0),
            ("ls docs", 0),
            ("cat README.md", 0),
            ("head wip/a.md", 0),
            ("sh .ccnavi/scripts/ccnavi-ticket.sh status", 0),
            ("sh /w/.ccnavi/scripts/ccnavi-ticket.sh --help", 2),
            ("sh /tmp/evil/.ccnavi/scripts/ccnavi-ticket.sh status", 2),
            ("sh ../x/.ccnavi/scripts/ccnavi-ticket.sh status", 2),
            ("wc --files0-from=.env", 2),
            ("cat -n README.md", 2),
            ("cat /dev/zero", 2),
            ("ls -R", 2),
            ("cat .git/config", 2),
            ("cat .GIT/config", 2),
            ("cat logs/state/ops/a.json", 2),
            ("cat key.pem", 2),
            ("cat ~/.aws/credentials", 2),
            ("cat credentials.json", 2),
            ("cat secrets/prod.txt", 2),
            ("less README.md", 2),
            ("rg --pre cmd x .", 2),
            ("echo hi", 2),
            ("grep x README.md", 2),
            ("cat a.md > b.md", 2),
            ("cat $(echo a)", 2),
            ("cat *.md", 2),
            ("ls && rm x", 2),
            ("cat .env", 2),
            ("sh .ccnavi/scripts/ccnavi-ticket.sh start i0001", 2),
        ):
            for mode in ("enable", "dry-run"):
                with self.subTest(command=command, mode=mode):
                    self.assertEqual(self.crash("Bash", {"command": command}, mode), expected)

    def test_the_status_script_must_be_this_workspaces(self):
        mine = os.path.join(self.root, ".ccnavi", "scripts", "ccnavi-ticket.sh")
        self.assertEqual(self.crash("Bash", {"command": f"sh {mine} status"}), 0)
        self.assertEqual(self.crash("Bash", {"command": f"sh {mine} --help"}, "dry-run"), 0)
        self.assertEqual(self.crash("Bash", {"command": f"sh {mine} start i0001"}), 2)

    def test_powershell_stops(self):
        self.assertEqual(self.crash("PowerShell", {"command": "pwd"}, "dry-run"), 2)

    def test_unknown_tools_stop_in_enable_and_pass_in_dry_run(self):
        self.assertEqual(self.crash("Mystery", {}), 2)
        self.assertEqual(self.crash("Mystery", {}, "dry-run"), 0)


REPORT_TOOLS = ("SubagentHandback", "StructuredOutput")


class ReportToolTest(unittest.TestCase):
    """サブエージェントの報告用のツールは、ccnavi の内部エラーで止めない。

    体が報告を返せないと、終われずに続けることになる。知らないツール全般を通す変更ではなく、
    `modes.REPORT_TOOLS` の名前だけ。入力を読めない障害までは通すと約束しない。
    """

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = directory.name

    def payload(self, tool):
        return {
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "tool_input": {"result": "ok"},
            "cwd": self.root,
        }

    def test_a_crash_in_the_judgement_does_not_stop_a_report(self):
        for tool in REPORT_TOOLS:
            for mode in ("enable", "dry-run"):
                with self.subTest(tool=tool, mode=mode):
                    with mock.patch.object(judge, "decide_before", side_effect=boom):
                        result, row = run(self.root, self.payload(tool), mode)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    # 通したことは言い、記録も残す
                    self.assertIn("[ccnavi] internal-error", result.stderr)
                    self.assertEqual(row["reason"], "internal-error")

    def test_a_crash_in_the_self_guard_does_not_stop_a_report(self):
        for tool in REPORT_TOOLS:
            with self.subTest(tool=tool):
                with mock.patch.object(selfguard, "before", side_effect=boom):
                    result, row = run(self.root, self.payload(tool))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(row["reason"], "internal-error")

    def test_a_failure_while_recording_does_not_stop_a_report(self):
        for tool in REPORT_TOOLS:
            with self.subTest(tool=tool):
                with mock.patch.object(cli_args, "_record", side_effect=boom):
                    result = run_raw(self.root, json.dumps(self.payload(tool)))
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_an_unknown_tool_still_stops_on_the_same_paths(self):
        for crash in (
            mock.patch.object(judge, "decide_before", side_effect=boom),
            mock.patch.object(selfguard, "before", side_effect=boom),
        ):
            with self.subTest(crash=crash.attribute):
                with crash:
                    result, _ = run(self.root, self.payload("UnknownTool"))
                self.assertEqual(result.returncode, 2, result.stderr)
        with mock.patch.object(cli_args, "_record", side_effect=boom):
            result = run_raw(self.root, json.dumps(self.payload("UnknownTool")))
        self.assertEqual(result.returncode, 2, result.stderr)

    def test_the_failsafe_and_fail_closed_agree(self):
        from ccnavi.hook import failsafe

        for tool in REPORT_TOOLS:
            self.assertEqual(modes.fail_closed(modes.ENABLE, tool), 0)
            self.assertEqual(modes.fail_closed(modes.DRY_RUN, tool), 0)
            self.assertTrue(
                failsafe.is_safe(hookio.Input(event="PreToolUse", tool_name=tool), self.root)
            )
        self.assertEqual(modes.fail_closed(modes.ENABLE, "UnknownTool"), 2)
        self.assertEqual(modes.fail_closed(modes.ENABLE, ""), 2)
        self.assertFalse(
            failsafe.is_safe(hookio.Input(event="PreToolUse", tool_name="UnknownTool"), self.root)
        )


class DeadlineTextTest(unittest.TestCase):
    """期限に届かなかった回の文。期限を見るのは実行前チェックだけ。"""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = directory.name

    def run_late(self, mode, command="echo hi"):
        payload = {**PRE, "tool_input": {"command": command}}
        with mock.patch.object(judge, "DEADLINE_SECONDS", -1):
            return run(self.root, payload, mode)

    def test_enable_stops_and_says_so(self):
        result, row = self.run_late("enable")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("[ccnavi] deadline-exceeded", result.stderr)
        self.assertIn("so it stopped it", result.stderr)
        self.assertIn("split the command", result.stderr)
        self.assertEqual(row["reason"], "deadline-exceeded")

    def test_dry_run_lets_it_through_and_does_not_say_stopped(self):
        result, row = self.run_late("dry-run", "ls")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[ccnavi] deadline-exceeded", result.stderr)
        self.assertIn("went through", result.stderr)
        self.assertNotIn("so it stopped it", result.stderr)
        self.assertEqual(row["reason"], "deadline-exceeded")

    def test_the_deadline_follows_the_tool_kind(self):
        """AT4：期限切れの結論もツールで決まる。Write は止め、安全な読み取りは通す。"""
        cases = (
            ("Write", {"file_path": "/tmp/x"}, 2),
            ("Edit", {"file_path": "/tmp/x"}, 2),
            ("Agent", {"description": "d"}, 2),
            ("Read", {"file_path": "/tmp/notes.md"}, 0),
            ("Read", {"file_path": "/home/u/.env"}, 2),
            ("Grep", {"pattern": "x", "path": "/tmp/notes"}, 0),
            ("Grep", {"pattern": "x", "path": "/home/u/.env"}, 2),
            ("Glob", {"pattern": "/tmp/notes/*.md"}, 0),
            ("PowerShell", {"command": "echo hi"}, 2),
            ("Bash", {"command": "ls"}, 0),
            ("Bash", {"command": "echo hi"}, 2),
            ("Bash", {"command": "cat .env"}, 2),
        )
        for tool, tool_input, expected in cases:
            for mode in ("enable", "dry-run"):
                with self.subTest(tool=tool, tool_input=tool_input, mode=mode):
                    payload = {**PRE, "tool_name": tool, "tool_input": tool_input, "cwd": self.root}
                    with mock.patch.object(judge, "DEADLINE_SECONDS", -1):
                        result, row = run(self.root, payload, mode)
                    self.assertEqual(result.returncode, expected, result.stderr)
                    self.assertEqual(row["reason"], "deadline-exceeded")


if __name__ == "__main__":
    unittest.main()
