"""実行ファイルの入口のスモークテスト。

標準入出力が UTF-8 で通ること、終了コードがプロセスの終了コードになることを、
tests/inproc.py を通さずに素の環境で 1 回ずつ見る。
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest

from tests import ROOT, exe, fixture_workspace


def spawn(*args, payload=""):
    environment = {
        **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
        "CCNAVI_LEARNING": "disable",
    }
    # コアファイルのバックアップと復元は切る。リポジトリ自身をワークスペースルートにして動くので、
    # 切らないと、ワークツリーで消した設定ファイルが `logs/state` のバックアップから書き戻される。
    environment["CCNAVI_GUARD_CORE_FILES"] = "disable"
    return subprocess.run(
        [exe.path(), "--root", fixture_workspace(), *args],
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
        # 入口の切り替えが有効なことが分かる。
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

    @unittest.skipIf(os.name == "nt", "疑似ターミナルは POSIX の話")
    def test_a_terminal_gets_the_usage_without_waiting_for_a_payload(self):
        """引数なしでターミナルから打つと、payload を待たずに使い方を出して 1 で終える。"""
        import pty

        master, slave = pty.openpty()
        try:
            proc = subprocess.Popen(
                [exe.path(), "--root", fixture_workspace()],
                stdin=slave,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=ROOT,
                env={k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            )
            os.close(slave)
            try:
                _, err = proc.communicate(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                self.fail("ターミナルの入力を待ち続けた")
        finally:
            os.close(master)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("hook", err.decode("utf-8", "replace"))


if __name__ == "__main__":
    unittest.main()
