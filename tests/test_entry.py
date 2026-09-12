"""`python -m ccnavi` の入口の煙テスト。ここだけは本物のプロセスを起こす。

他のテストは tests/inproc.py で cli.run を同じプロセスの中で呼ぶ。速いが、
パッケージとして起動できること、標準入出力が UTF-8 に付け替わること、
終了コードがプロセスの終了コードになることは、プロセスを起こさないと分からない。
その 3 つをここで 1 回ずつ見る。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(ROOT, "testdata", "rules.yml")


def spawn(*args, payload=""):
    environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
    # コンソールのコードページに関係なく UTF-8 で通ることを見たいので、
    # 親から Python の入出力の指定は渡さない。
    environment.pop("PYTHONIOENCODING", None)
    environment.pop("PYTHONUTF8", None)
    return subprocess.run(
        [sys.executable, "-m", "ccnavi", "--rules", RULES, "--log", "", *args],
        input=payload.encode("utf-8"),
        capture_output=True,
        cwd=ROOT,
        env=environment,
    )


class EntryTest(unittest.TestCase):
    def test_a_verdict_comes_back_as_utf8_json_with_exit_zero(self):
        payload = json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "git push origin main"},
            },
            ensure_ascii=False,
        )
        done = spawn("--mode", "enable", payload=payload)

        self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", "replace"))
        out = json.loads(done.stdout.decode("utf-8"))["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("DENY_COMMAND_PATTERN", out["permissionDecisionReason"])

    def test_japanese_in_the_payload_survives_the_round_trip(self):
        # 判定の理由は対象を名指しする。日本語を含む対象がそのまま返ることで、
        # 入口の付け替えが効いていることが分かる。
        command = "git push origin 統合先"
        payload = json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": command},
            },
            ensure_ascii=False,
        )
        done = spawn("--mode", "enable", payload=payload)

        self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", "replace"))
        out = json.loads(done.stdout.decode("utf-8"))["hookSpecificOutput"]
        self.assertIn(command, out["permissionDecisionReason"])

    def test_no_payload_fails_with_a_nonzero_exit(self):
        done = spawn("--mode", "enable", payload="")

        self.assertNotEqual(done.returncode, 0)
        self.assertIn("hook", done.stderr.decode("utf-8", "replace"))


if __name__ == "__main__":
    unittest.main()
