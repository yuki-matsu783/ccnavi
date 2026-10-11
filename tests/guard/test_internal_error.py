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

from ccnavi.hook import events, judge
from tests import ROOT
from tests.inproc import run_ccnavi


def run(root, payload, mode="enable"):
    environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
    environment["CCNAVI_GUARD_CORE_FILES"] = "disable"
    environment.pop("CLAUDE_PROJECT_DIR", None)
    log = os.path.join(root, "decisions.jsonl")
    result = run_ccnavi(
        ["--root", root, "--log", log, "--mode", mode],
        input=json.dumps(payload),
        cwd=ROOT,
        env=environment,
    )
    with open(log, encoding="utf-8") as f:
        row = json.loads(f.read().splitlines()[-1])
    return result, row


def boom(*_args, **_kwargs):
    raise RuntimeError("想定外")


PRE = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}}


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

    def test_pre_tool_use_in_dry_run_lets_the_call_through(self):
        with mock.patch.object(judge, "decide_before", side_effect=boom):
            result, row = run(self.root, PRE, mode="dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
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

    def test_unusable_payload_says_what_to_do(self):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        result = run_ccnavi(
            ["--root", self.root, "--log", "", "--mode", "enable"],
            input="not json",
            cwd=ROOT,
            env=environment,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("[ccnavi] payload-unusable", result.stderr)
        self.assertIn("Tell the user", result.stderr)


if __name__ == "__main__":
    unittest.main()
