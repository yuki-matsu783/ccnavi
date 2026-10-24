"""`ccnavi-idle.sh`（放置したセッションの引き継ぎ促しの待機）の受入テスト。

実行ファイルの代わりに、ソースから `python -m ccnavi` を起こす小さな sh を置いて、
Stop の payload から「待つ → 起きたら聞く → 起こす（終了コード 2 と指示）」までを通しで見る。
待ち時間は `CCNAVI_HANDOFF_WAIT_SECONDS` で 1 秒に縮める。
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

from tests import ROOT

SHELL = shutil.which("sh") or shutil.which("bash")
SCRIPTS = os.path.join(ROOT, ".ccnavi", "scripts")
SESSION = "sess-sh"


def transcript_row(tokens: int) -> str:
    usage = {
        "input_tokens": 2,
        "cache_read_input_tokens": tokens - 12,
        "cache_creation_input_tokens": 10,
        "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 10},
    }
    return json.dumps({"type": "assistant", "message": {"usage": usage}})


class IdleShTest(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="ccnavi-idle-sh-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        shutil.copytree(SCRIPTS, os.path.join(self.ws, ".ccnavi", "scripts"))
        self.exe = os.path.join(self.ws, "fake-ccnavi")
        with open(self.exe, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(
                f'#!/bin/sh\nPYTHONPATH="{ROOT}/src" exec "{sys.executable}" -m ccnavi "$@"\n'
            )
        os.chmod(self.exe, os.stat(self.exe).st_mode | stat.S_IXUSR)
        self.transcript = os.path.join(self.ws, "t.jsonl")

    def run_idle(self, tokens: int, **env):
        with open(self.transcript, "w", encoding="utf-8") as handle:
            handle.write(transcript_row(tokens) + "\n")
        payload = json.dumps(
            {
                "hook_event_name": "Stop",
                "session_id": SESSION,
                "transcript_path": self.transcript,
            }
        )
        script = os.path.join(self.ws, ".ccnavi", "scripts", "ccnavi-idle.sh")
        base = {k: v for k, v in os.environ.items() if not k.startswith(("CCNAVI_", "CLAUDE_"))}
        return subprocess.run(
            [SHELL, script, "start"],
            input=payload,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=self.ws,
            env={
                **base,
                "CLAUDE_PROJECT_DIR": self.ws,
                "CCNAVI_ENTRY_PATH": self.exe,
                "CCNAVI_HANDOFF_WAIT_SECONDS": "1",
                "CCNAVI_LEARNING": "disable",
                **env,
            },
        )

    def test_閾値以上で放置されたら終了コード2で指示を出す(self):
        result = self.run_idle(500_000)

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("[idle-handoff]", result.stderr)
        self.assertIn("scratchpad/", result.stderr)

    def test_閾値に届かなければ待たずに0で終わる(self):
        result = self.run_idle(10_000)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")

    def test_発火したあとはユーザが打つまで始め直さない(self):
        first = self.run_idle(500_000)
        again = self.run_idle(500_000)

        self.assertEqual(first.returncode, 2)
        self.assertEqual(again.returncode, 0)

    def test_見ている人がいない実行は待たずに0で終わる(self):
        result = self.run_idle(500_000, CLAUDE_CODE_ENTRYPOINT="sdk-cli")

        self.assertEqual(result.returncode, 0)

    def test_実行ファイルが無ければ何もせず0で終わる(self):
        result = self.run_idle(500_000, CCNAVI_ENTRY_PATH=os.path.join(self.ws, "none"))

        self.assertEqual(result.returncode, 0)

    def test_環境変数を0にすると無効(self):
        result = self.run_idle(500_000, CCNAVI_HANDOFF_SUGGEST_TOKENS="0")

        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
