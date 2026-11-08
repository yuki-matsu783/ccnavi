"""判定の期限（REQ-CMN-08）の受入テスト。

期限は判定がルールを 1 つ照合するたびに見る。ここでは期限を環境変数
（CCNAVI_HOOK_DEADLINE_SECONDS）でごく短くして、実時間を待たずに期限切れの経路を確かめる。

- enable は終了コード 2、dry-run は安全な読み取りだけ 0
- 標準エラーに期限の文面、記録の `reason` に `deadline-exceeded`
- 期限に達した回は、標準出力に allow（や deny の応答）を出さない
- 期限切れの結論はツールの種類で決まる（書き込みは止め、安全な読み取りは通す）

どの照合の回で期限に達しても応答を出さないこと、期限ちょうどは超えていないことは、時計を差し替えられる
Go のテスト（go/internal/hook/judge の TestDeadline）が確かめる。
実時間の代表入力は、期限の 1/3 に収まることを緩い上限で見る。測定は tests/core/bench_deadline.py。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest

from tests import ROOT, common_path
from tests.inproc import run_ccnavi

# 判定の期限の既定（秒）。
DEADLINE_SECONDS = 3.0
# 最初の照合で超える期限（秒）。
LATE = "0.000001"


def payload(command: str, tool: str = "Bash", tool_input: dict | None = None) -> str:
    return json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "tool_input": tool_input if tool_input is not None else {"command": command},
        }
    )


class DeadlineTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = os.path.realpath(directory.name)
        target = common_path(self.root, "rules")
        os.makedirs(os.path.dirname(target))
        shutil.copyfile(os.path.join(ROOT, "tests", "fixtures", "rules.yml"), target)

    def run_late(self, command, mode="enable", tool="Bash", tool_input=None):
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
            "CCNAVI_GUARD_CORE_FILES": "disable",
            "CCNAVI_HOOK_DEADLINE_SECONDS": LATE,
        }
        environment.pop("CLAUDE_PROJECT_DIR", None)
        return run_ccnavi(
            ["--root", self.root, "--mode", mode],
            input=payload(command, tool, tool_input),
            cwd=ROOT,
            env=environment,
        )

    def last_record(self):
        with open(os.path.join(self.root, "logs", "decisions.jsonl"), encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()][-1]

    def test_enable_stops_with_exit_code_2(self):
        for command in ("echo hi", "git push origin main", "cat .env"):
            with self.subTest(command=command):
                result = self.run_late(command)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")

    def test_enable_says_it_stopped(self):
        result = self.run_late("echo hi")
        self.assertIn("[ccnavi] deadline-exceeded", result.stderr)
        self.assertIn("期限（", result.stderr)
        self.assertIn("止めました", result.stderr)
        self.assertIn("短く分けて", result.stderr)

    def test_dry_run_lets_a_safe_read_through_and_does_not_say_stopped(self):
        result = self.run_late("ls", mode="dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("[ccnavi] deadline-exceeded", result.stderr)
        self.assertIn("通しました", result.stderr)
        self.assertNotIn("止めました", result.stderr)

    def test_record_says_deadline_exceeded(self):
        self.run_late("echo hi")
        row = self.last_record()
        self.assertEqual(row["decision"], "skip")
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
                    result = self.run_late("", mode, tool, tool_input)
                    self.assertEqual(result.returncode, expected, result.stderr)
                    self.assertEqual(self.last_record()["reason"], "deadline-exceeded")


class RealTimeTest(unittest.TestCase):
    """実時間の代表入力。期限の 1/3 に収まる大きさで、上限は緩く取る。

    上限を期限そのものにするのは、遅い機械で揺れないようにするため。
    """

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = os.path.realpath(directory.name)
        target = common_path(self.root, "rules")
        os.makedirs(os.path.dirname(target))
        shutil.copyfile(os.path.join(ROOT, "tests", "fixtures", "rules.yml"), target)

    def timed(self, command):
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        environment["CCNAVI_GUARD_CORE_FILES"] = "disable"
        environment.pop("CLAUDE_PROJECT_DIR", None)
        start = time.perf_counter()
        result = run_ccnavi(
            ["--root", self.root, "--mode", "enable"],
            input=payload(command),
            cwd=ROOT,
            env=environment,
        )
        return time.perf_counter() - start, result

    def test_long_command_finishes_well_within_the_deadline(self):
        seconds, result = self.timed("echo " + "a" * 3_000)
        self.assertNotIn("deadline-exceeded", result.stderr)
        self.assertLess(seconds, DEADLINE_SECONDS)

    def test_many_substitutions_finish_well_within_the_deadline(self):
        seconds, result = self.timed("echo " + "$(:) " * 500)
        self.assertNotIn("deadline-exceeded", result.stderr)
        self.assertLess(seconds, DEADLINE_SECONDS)


if __name__ == "__main__":
    unittest.main()
