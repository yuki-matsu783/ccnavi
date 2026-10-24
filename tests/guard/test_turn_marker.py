"""ターン中の印（`records/turn.py`）の受入テスト。

印はセッションごとのファイルで、ユーザの発言で置き、ツール呼び出しで更新時刻を新しくし、
Stop と中断で消す。VS Code 拡張は `--explain --json` の `turns` でこれを読む。

見るのは次の 3 つ。

- 発言から Stop までの間だけ、印がある
- 中断や、プロセスが落ちたことでターンが閉じなくても、古い印は数えない
- 並行するセッションは互いの印を書き換えも消しもしない
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from ccnavi.hook import events
from ccnavi.records import turn
from tests import ROOT
from tests.inproc import run_ccnavi


class TurnRecordTest(unittest.TestCase):
    """`records/turn.py` そのもの。"""

    def setUp(self):
        self.state = tempfile.mkdtemp(prefix="ccnavi-turn-")
        self.addCleanup(shutil.rmtree, self.state, ignore_errors=True)

    def sessions(self, now=None):
        return [a.session for a in turn.active(self.state, now)]

    def test_始めたセッションがターン中に出る(self):
        turn.begin(self.state, "s1", now=1000.0)

        found = turn.active(self.state, now=1010.0)

        self.assertEqual([a.session for a in found], ["s1"])
        self.assertEqual(found[0].since, 1000.0)

    def test_終わったセッションは出ない(self):
        turn.begin(self.state, "s1")

        turn.end(self.state, "s1")

        self.assertEqual(self.sessions(), [])

    def test_終えていない印が無いセッションを終えても何も起きない(self):
        turn.end(self.state, "nobody")
        turn.clear(self.state, "nobody")

        self.assertEqual(self.sessions(), [])

    def test_更新が無いまま古くなった印は数えない(self):
        # 中断や、ターンの途中でプロセスが落ちたとき、Stop は来ない。
        turn.begin(self.state, "s1", now=1000.0)
        os.utime(os.path.join(self.state, "turn-s1.json"), (1000.0, 1000.0))

        self.assertEqual(self.sessions(now=1000.0 + turn.STALE_SECONDS - 1), ["s1"])
        self.assertEqual(self.sessions(now=1000.0 + turn.STALE_SECONDS), [])

    def test_ツール呼び出しの更新で古くならない(self):
        turn.begin(self.state, "s1", now=1000.0)
        os.utime(os.path.join(self.state, "turn-s1.json"), (1000.0, 1000.0))

        turn.touch(self.state, "s1", now=1000.0 + turn.STALE_SECONDS - 5)

        later = 1000.0 + turn.STALE_SECONDS + 60
        self.assertEqual(self.sessions(now=later), ["s1"])

    def test_更新は無い印を作らない(self):
        # Stop の直後に走ったサブエージェントの呼び出しが、消した印を作り直さない。
        turn.begin(self.state, "s1")
        turn.end(self.state, "s1")

        turn.touch(self.state, "s1")

        self.assertEqual(self.sessions(), [])
        self.assertFalse(os.path.exists(os.path.join(self.state, "turn-s1.json")))

    def test_並行するセッションは互いの印に触れない(self):
        turn.begin(self.state, "s1")
        turn.begin(self.state, "s2")

        turn.end(self.state, "s1")

        self.assertEqual(self.sessions(), ["s2"])

    def test_壊れた印と形の違う印は数えず読むだけで消さない(self):
        turn.begin(self.state, "ok")
        for name, text in (
            ("turn-broken.json", "{not json"),
            ("turn-list.json", "[]"),
            ("turn-nosince.json", json.dumps({"session": "x"})),
            ("turn-badsince.json", json.dumps({"since": "yesterday"})),
            ("turn-bool.json", json.dumps({"since": True})),
        ):
            with open(os.path.join(self.state, name), "w", encoding="utf-8") as f:
                f.write(text)

        self.assertEqual(self.sessions(), ["ok"])
        self.assertTrue(os.path.exists(os.path.join(self.state, "turn-broken.json")))

    def test_置き場が無い_書けない_空でも例外にしない(self):
        missing = os.path.join(self.state, "nowhere", "state")
        blocker = os.path.join(self.state, "file")
        with open(blocker, "w", encoding="utf-8") as f:
            f.write("x")

        for state in ("", missing, blocker):
            with self.subTest(state=state):
                turn.begin(state, "s1")
                turn.touch(state, "s1")
                turn.end(state, "s1")
                turn.clear(state, "s1")
                self.assertEqual(turn.active(state), [])
        turn.begin(self.state, "")
        self.assertEqual(self.sessions(), [])


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
        return sorted(a.session for a in turn.active(self.state))

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
        self.run_hook("UserPromptSubmit", prompt="お願いします")

        with mock.patch.object(events, "_finish_nudge", return_value="続けてください"):
            stopped = self.run_hook("Stop")

        self.assertIn("block", stopped.stdout)
        self.assertEqual(self.active(), ["s1"])
        self.run_hook("Stop")
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

    def test_再開で前のプロセスの印を捨てる(self):
        self.run_hook("UserPromptSubmit", prompt="お願いします")

        self.run_hook("SessionStart", source="resume")

        self.assertEqual(self.active(), [])

    def test_compact_では印を捨てない(self):
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
