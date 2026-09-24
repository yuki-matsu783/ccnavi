"""記録は `<root>/logs/log.jsonl`、控えは `<root>/logs/state`。空文字の口は無い（ADR-0084 の A4）。

`CCNAVI_LOG=""` は「記録しない」、`CCNAVI_STATE=""` は「控えを持たない」と読まれていた。
6 つの env を廃止したので、空文字を入れても既定の置き場に書かれる。

診断のフラグ `--log ""` / `--state ""` は残る（テスト 20 本以上がこれで置き場を指している）。
ここは **フラグを渡さずに** 起動する。

実装前は赤。赤の理由は「まだ `CCNAVI_LOG` / `CCNAVI_STATE` の空文字を読んでいる」こと。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from tests.inproc import run_ccnavi


class EmptyEnvStillWritesTheRecordTest(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="ccnavi-log-place-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        os.makedirs(os.path.join(self.ws, ".ccnavi", "common"))
        with open(
            os.path.join(self.ws, ".ccnavi", "common", "rules.yml"), "w", encoding="utf-8"
        ) as f:
            f.write('{"version": 1}')

    def hook(self, event, tool, env):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment.pop("CLAUDE_PROJECT_DIR", None)
        environment.update(env)
        payload = {
            "hook_event_name": event,
            "tool_name": tool,
            "cwd": self.ws,
            "session_id": "s1",
            "tool_input": {"command": "ls"} if tool else {},
        }
        done = run_ccnavi(
            ["--root", self.ws, "--mode", "enable"], input=json.dumps(payload), env=environment
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_an_empty_log_env_still_writes_to_the_default_place(self):
        """`CCNAVI_LOG=""` を入れても、判定の記録は `logs/log.jsonl` に 1 件書かれる。"""
        self.hook("PreToolUse", "Bash", {"CCNAVI_LOG": ""})

        path = os.path.join(self.ws, "logs", "log.jsonl")
        self.assertTrue(os.path.isfile(path), "logs/log.jsonl が書かれていない")
        with open(path, encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(records), 1, records)
        self.assertEqual(records[0]["tool"], "Bash")

    def test_an_empty_state_env_still_keeps_the_state(self):
        """`CCNAVI_STATE=""` を入れても、控えは `logs/state/` に置かれる。"""
        env = {"CCNAVI_STATE": ""}
        self.hook("UserPromptSubmit", "", env)
        self.hook("PostToolUse", "Bash", env)

        self.assertTrue(os.path.isfile(os.path.join(self.ws, "logs", "state", "s1.json")))


if __name__ == "__main__":
    unittest.main()
