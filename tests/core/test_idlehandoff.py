"""放置したセッションの引き継ぎ促し（`ccnavi idle`）の受入テスト。

待機の始めと起こす指示・ユーザの入力での取り消し・知らせが 1 セッション 1 回であることを、
`ccnavi idle` を起こして見る。会話の大きさの読み方・閾値と基準の刻み・状態遷移の細部は
`go/internal/records/handoff` の go test が見る。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from tests.inproc import run_ccnavi

SESSION = "sess-1"
# 起こす指示に付ける目印（ユーザが打った文と見分ける）。
MARKER = "[idle-handoff]"


def assistant(tokens: int, short: int = 0, long: int = 0, sidechain: bool = False) -> str:
    usage = {
        "input_tokens": 2,
        "cache_read_input_tokens": tokens - 2 - short - long,
        "cache_creation_input_tokens": short + long,
        "cache_creation": {
            "ephemeral_5m_input_tokens": short,
            "ephemeral_1h_input_tokens": long,
        },
    }
    row = {"type": "assistant", "isSidechain": sidechain, "message": {"usage": usage}}
    return json.dumps(row)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ccnavi-idle-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.state = os.path.join(self.dir, "state")

    def transcript(self, *rows: str) -> str:
        path = os.path.join(self.dir, "t.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(rows) + "\n")
        return path


class CliTest(Fixture):
    def setUp(self):
        super().setUp()
        self.root = os.path.join(self.dir, "ws")
        os.makedirs(self.root)

    def payload(self, event="Stop", **extra):
        body = {
            "hook_event_name": event,
            "session_id": SESSION,
            "transcript_path": self.transcript(assistant(500_000, long=10)),
            **extra,
        }
        return json.dumps(body)

    def idle(self, verb, payload="", env=None, *rest):
        return run_ccnavi(
            ["--root", self.root, "idle", verb, *rest],
            input=payload,
            env={"CCNAVI_HANDOFF_WAIT_SECONDS": "7", **(env or {})},
        )

    def started(self):
        result = self.idle("start", self.payload())
        self.assertEqual(result.returncode, 0, result.stderr)
        return dict(line.split(" ", 1) for line in result.stdout.splitlines())

    def test_startからcheckで起こし_resetで取り消す(self):
        first = self.started()
        self.assertEqual(first["wait"], "7")
        self.assertEqual(first["session"], SESSION)
        self.assertTrue(first["token"])

        fired = self.idle("check", "", None, SESSION, first["token"], "7")
        self.assertTrue(fired.stdout.startswith("fire\n"), fired.stdout)
        self.assertIn(MARKER, fired.stdout)

        # 新しい待機を始めてから、ユーザの入力で取り消すと起こさない。
        other = self.idle("start", self.payload(session_id="sess-3"))
        second = dict(line.split(" ", 1) for line in other.stdout.splitlines())
        reset = self.payload("UserPromptSubmit", prompt="続けます", session_id="sess-3")
        self.idle("reset", reset)
        skipped = self.idle("check", "", None, "sess-3", second["token"], "7")
        self.assertTrue(skipped.stdout.startswith("skip"), skipped.stdout)

    def test_始めない場合は理由を返す(self):
        sub = self.idle("start", self.payload(agent_id="agent-1"))
        sdk = self.idle("start", self.payload(), {"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"})
        headless = self.idle("start", self.payload(), {"CLAUDE_CODE_SESSION_ATTENDED": "0"})
        off = self.idle("start", self.payload(), {"CCNAVI_HANDOFF_SUGGEST_TOKENS": "0"})
        high = self.idle("start", self.payload(), {"CCNAVI_HANDOFF_SUGGEST_TOKENS": "600000"})

        self.assertEqual(sub.stdout, "skip subagent\n")
        self.assertEqual(sdk.stdout, "skip unattended\n")
        self.assertEqual(headless.stdout, "skip unattended\n")
        self.assertEqual(off.stdout, "skip disabled\n")
        self.assertEqual(high.stdout, "skip not-due\n")

    def test_noticeはsystemMessageのJSONを1回だけ出し_起こした後は報告を1回だけ出す(self):
        first = self.idle("notice", self.payload())
        second = self.idle("notice", self.payload())
        self.assertIn("systemMessage", json.loads(first.stdout))
        self.assertEqual(second.stdout, "")

        # 別のセッションで、起こした後の報告を見る。
        started = self.idle("start", self.payload(session_id="sess-2"))
        lines = dict(line.split(" ", 1) for line in started.stdout.splitlines())
        self.idle("check", "", None, "sess-2", lines["token"], "7")
        reported = self.idle("notice", self.payload(session_id="sess-2"))
        again = self.idle("notice", self.payload(session_id="sess-2"))
        self.assertIn("放置されたため", json.loads(reported.stdout)["systemMessage"])
        self.assertEqual(again.stdout, "")

    def test_payloadが無くても止まらない(self):
        for verb in ("notice", "start", "reset"):
            with self.subTest(verb=verb):
                self.assertEqual(self.idle(verb, "").returncode, 0)


if __name__ == "__main__":
    unittest.main()
