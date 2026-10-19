"""判定の期限（REQ-CMN-08）の受入テスト。

期限は `judge.decide_before` がルールを 1 つ照合するたびに見る。ここでは時計を差し替えて、
実時間を待たずに期限切れの経路を確かめる。

- enable は終了コード 2、dry-run は 0
- 標準エラーに期限の文面、記録の `reason` に `deadline-exceeded`
- 期限に達した回は、標準出力に allow（や deny の応答）を出さない

時計は 2 か所で差し替える。期限を決める `cli.time`（呼び出しの頭で 1 回）と、確認する
`judge.time`（ルールごと）。実時間の代表入力は、期限の 1/3 に収まることを緩い上限で確かめる。
測定は tests/core/bench_deadline.py。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from ccnavi.entry import cli
from ccnavi.hook import judge
from tests import ROOT, common_path
from tests.inproc import run_ccnavi

START = 100.0
EXPIRED = START + judge.DEADLINE_SECONDS + 1


class Clock:
    """`time.monotonic` だけを持つ差し替え用の時計。

    最初の `checks` 回は期限に届く前の時刻を返し、それ以降は期限を過ぎた時刻を返す。
    呼ばれた回数は `calls` に残る。
    """

    def __init__(self, checks: int, before: float = START, after: float = EXPIRED) -> None:
        self.checks, self.before, self.after, self.calls = checks, before, after, 0

    def monotonic(self) -> float:
        self.calls += 1
        return self.before if self.calls <= self.checks else self.after


class Start:
    """期限を決める側（cli）の時計。いつも START を返す。"""

    @staticmethod
    def monotonic() -> float:
        return START


def payload(command: str) -> str:
    return json.dumps(
        {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}}
    )


class DeadlineTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = os.path.realpath(directory.name)
        target = common_path(self.root, "rules")
        os.makedirs(os.path.dirname(target))
        shutil.copyfile(os.path.join(ROOT, "tests", "fixtures", "rules.yml"), target)

    def run_with(self, command, clock, mode="enable"):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment["CCNAVI_GUARD_CORE_FILES"] = "disable"
        environment.pop("CLAUDE_PROJECT_DIR", None)
        with (
            mock.patch.object(cli, "time", Start),
            mock.patch.object(judge, "time", clock),
        ):
            return run_ccnavi(
                ["--root", self.root, "--mode", mode],
                input=payload(command),
                cwd=ROOT,
                env=environment,
            )

    def last_record(self):
        with open(os.path.join(self.root, "logs", "decisions.jsonl"), encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()][-1]

    def test_enable_stops_with_exit_code_2(self):
        result = self.run_with("echo hi", Clock(0))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_dry_run_lets_it_through(self):
        result = self.run_with("ls", Clock(0), mode="dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("deadline-exceeded", result.stderr)

    def test_stderr_names_the_deadline(self):
        result = self.run_with("echo hi", Clock(0))
        self.assertIn("[ccnavi] deadline-exceeded", result.stderr)
        self.assertIn(f"({judge.DEADLINE_SECONDS:g} s)", result.stderr)

    def test_record_says_deadline_exceeded(self):
        self.run_with("echo hi", Clock(0))
        row = self.last_record()
        self.assertEqual(row["decision"], "skip")
        self.assertEqual(row["reason"], "deadline-exceeded")

    def test_reaching_exactly_the_deadline_is_not_exceeded(self):
        # 比べるのは `>`。期限ちょうどは超えていない。
        clock = Clock(0, after=START + judge.DEADLINE_SECONDS)
        result = self.run_with("echo hi", clock)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("deadline-exceeded", result.stderr)

    def test_a_call_that_hits_the_deadline_never_gets_a_verdict(self):
        # 照合の回数を数え、どの回で期限に達しても、応答を出さずに止めることを確かめる。
        # 止めるコマンド（deny）も、通すコマンド（allow）も同じ。
        for command in ("echo hi", "git push origin main", "cat .env"):
            with self.subTest(command=command):
                counting = Clock(10**9)
                done = self.run_with(command, counting)
                checks = counting.calls
                self.assertGreater(checks, 0)
                self.assertNotIn("deadline-exceeded", done.stderr)
                for hit in range(checks):
                    with self.subTest(command=command, hit=hit):
                        result = self.run_with(command, Clock(hit))
                        self.assertEqual(result.returncode, 2, result.stderr)
                        self.assertEqual(result.stdout, "")
                        self.assertIn("deadline-exceeded", result.stderr)
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
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
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
        self.assertLess(seconds, judge.DEADLINE_SECONDS)

    def test_many_substitutions_finish_well_within_the_deadline(self):
        seconds, result = self.timed("echo " + "$(:) " * 500)
        self.assertNotIn("deadline-exceeded", result.stderr)
        self.assertLess(seconds, judge.DEADLINE_SECONDS)


if __name__ == "__main__":
    unittest.main()
