"""ターン中の印の受入テスト。

印はセッションごとのファイルで、ユーザの発言で置き、ツール呼び出しで更新時刻を新しくし、
Stop と中断で消す。VS Code 拡張は `--explain --json` の `turns` でこれを読む。

見るのは次の 2 つ。

- 発言から Stop までの間だけ、印がある
- 並行するセッションは互いの印を書き換えも消しもしない

印の読み書きそのもの（古い印・壊れた印・置き場が無いとき）は Go のテスト
（go/internal/records/turn）が確かめる。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest

from tests import ROOT
from tests.inproc import run_ccnavi

RULES = os.path.join(".ccnavi", "config", "common", "rules.yml")


class TurnWiringTest(unittest.TestCase):
    """hook の各イベントが印を置き、消すこと。"""

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="ccnavi-turnwire-")
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.state = os.path.join(self.repo, "logs", "state")
        os.makedirs(os.path.join(self.repo, ".ccnavi", "config", "common"))

    def run_hook(self, event, session="s1", mode="enable", **body):
        payload = {"hook_event_name": event, "session_id": session, "tool_name": "Bash"}
        payload["tool_input"] = {"command": "ls"}
        payload.update(body)
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment["CCNAVI_LEARNING"] = "disable"
        # 動作モードを切るのは環境変数だけ（プロジェクト内から来る `--mode disable` は無視される）。
        environment["CCNAVI_MODE"] = mode
        return run_ccnavi(
            ["--root", self.repo],
            input=json.dumps(payload),
            cwd=ROOT,
            env=environment,
        )

    def marker(self, session="s1"):
        return os.path.join(self.state, f"turn-{session}.json")

    def active(self):
        """ターン中のセッション。ボードの JSON（`--explain --json` の turns）から読む。"""
        done = run_ccnavi(["--explain", "--json", "--root", self.repo], cwd=ROOT)
        return sorted(t["session"] for t in json.loads(done.stdout)["turns"])

    def test_発言から_Stop_までの間だけ印がある(self):
        self.run_hook("SessionStart", source="startup")
        self.assertEqual(self.active(), [])

        self.run_hook("UserPromptSubmit", prompt="お願いします")
        self.assertEqual(self.active(), ["s1"])

        self.run_hook("PreToolUse")
        self.run_hook("PostToolUse")
        self.assertEqual(self.active(), ["s1"])

        self.run_hook("Stop")
        self.assertEqual(self.active(), [])

    def test_ツール呼び出しで更新時刻が新しくなる(self):
        self.run_hook("UserPromptSubmit", prompt="お願いします")
        old = time.time() - 600
        os.utime(self.marker(), (old, old))

        self.run_hook("PreToolUse")

        self.assertGreater(os.stat(self.marker()).st_mtime, old + 300)

    def test_止めた_Stop_ではターンが続くので印を残す(self):
        # 促しで Stop を止めると、モデルは同じターンの続きを動かす。続きの間も印が要る。
        # 2 回に 1 度止める Stop のルールで、2 回目の Stop を止めさせる。
        rule = {
            "id": "nudge",
            "match": "Stop",
            "glob": "*",
            "additionalContext": "続ける",
            "every": 2,
        }
        with open(os.path.join(self.repo, RULES), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "allow": [rule]}, f)
        self.run_hook("UserPromptSubmit", prompt="お願いします")
        self.assertNotIn("block", self.run_hook("Stop").stdout)
        self.run_hook("UserPromptSubmit", prompt="続きを")

        stopped = self.run_hook("Stop")

        self.assertIn("block", stopped.stdout)
        self.assertEqual(self.active(), ["s1"])
        self.run_hook("Stop", stop_hook_active=True)
        self.assertEqual(self.active(), [])

    def test_ユーザの中断で印を消す(self):
        # 中断では Stop が来ないことがある。
        self.run_hook("UserPromptSubmit", prompt="お願いします")

        self.run_hook("PostToolUseFailure", is_interrupt=True)

        self.assertEqual(self.active(), [])

    def test_サブエージェントの中断はターンを閉じない(self):
        self.run_hook("UserPromptSubmit", prompt="お願いします")

        self.run_hook("PostToolUseFailure", is_interrupt=True, agent_id="sub1")

        self.assertEqual(self.active(), ["s1"])

    def test_通常の失敗ではターンは続く(self):
        self.run_hook("UserPromptSubmit", prompt="お願いします")

        self.run_hook("PostToolUseFailure", error="boom")

        self.assertEqual(self.active(), ["s1"])

    def test_バックグラウンド作業の完了通知もターンを始める(self):
        self.run_hook(
            "UserPromptSubmit",
            prompt="<task-notification>\n<status>completed</status>\n</task-notification>",
        )

        self.assertEqual(self.active(), ["s1"])

    def test_Stop_のあとのツール呼び出しは印を作り直さない(self):
        self.run_hook("UserPromptSubmit", prompt="お願いします")
        self.run_hook("Stop")

        self.run_hook("PostToolUse", agent_id="sub1")

        self.assertEqual(self.active(), [])

    def test_再開で前のプロセスの印を破棄する(self):
        self.run_hook("UserPromptSubmit", prompt="お願いします")

        self.run_hook("SessionStart", source="resume")

        self.assertEqual(self.active(), [])

    def test_compact_では印を破棄しない(self):
        # 同じプロセスで、ターンの途中に来ることがある。
        self.run_hook("UserPromptSubmit", prompt="お願いします")

        self.run_hook("SessionStart", source="compact")

        self.assertEqual(self.active(), ["s1"])

    def test_並行するセッションの印は互いに独立(self):
        self.run_hook("UserPromptSubmit", session="a", prompt="一つ目")
        self.run_hook("UserPromptSubmit", session="b", prompt="二つ目")
        self.assertEqual(self.active(), ["a", "b"])

        self.run_hook("Stop", session="a")
        self.assertEqual(self.active(), ["b"])

        self.run_hook("SessionStart", session="a", source="resume")
        self.assertEqual(self.active(), ["b"])

    def test_判定を切っているモードでは印を置かない(self):
        self.run_hook("UserPromptSubmit", mode="disable", prompt="お願いします")

        self.assertEqual(self.active(), [])

    def test_壊れた印があってもフックは止まらない(self):
        os.makedirs(self.state, exist_ok=True)
        with open(self.marker(), "w", encoding="utf-8") as f:
            f.write("{not json")

        for event in ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"):
            with self.subTest(event=event):
                done = self.run_hook(event, prompt="お願いします")
                self.assertNotIn("internal-error", done.stderr)

    def test_ボードの_JSON_に_turns_が出る(self):
        self.run_hook("UserPromptSubmit", session="a", prompt="一つ目")
        self.run_hook("UserPromptSubmit", session="b", prompt="二つ目")
        self.run_hook("Stop", session="a")

        done = run_ccnavi(["--explain", "--json", "--root", self.repo], cwd=ROOT)

        board = json.loads(done.stdout)
        self.assertEqual([t["session"] for t in board["turns"]], ["b"])
        self.assertEqual(set(board["turns"][0]), {"session", "since", "seen"})

    def test_ターン中でなければ_turns_は空(self):
        done = run_ccnavi(["--explain", "--json", "--root", self.repo], cwd=ROOT)

        self.assertEqual(json.loads(done.stdout)["turns"], [])


if __name__ == "__main__":
    unittest.main()
