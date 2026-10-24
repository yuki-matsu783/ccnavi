"""放置したセッションの引き継ぎ促し（records.idlehandoff と `ccnavi idle`）の受入テスト。

会話の大きさとキャッシュの保持時間をトランスクリプトから読むこと、閾値と基準の刻み、待機の
状態遷移（始める・ユーザの入力で取り消す・発火したら始め直さない）、ユーザへの知らせが 1 セッション
1 回であることを見る。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from ccnavi.records import idlehandoff
from tests.inproc import run_ccnavi

SESSION = "sess-1"


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


class UsageTest(Fixture):
    def test_最新の応答の入力の合計を会話の大きさとする(self):
        path = self.transcript(assistant(1000, long=10), assistant(2500, long=10))

        usage = idlehandoff.usage_of(path)

        self.assertEqual(usage.tokens, 2500)

    def test_サブエージェントの行は数えない(self):
        path = self.transcript(assistant(1000, long=10), assistant(9000, long=10, sidechain=True))

        self.assertEqual(idlehandoff.usage_of(path).tokens, 1000)

    def test_直近の書き込みが5分側なら保持時間は5分(self):
        path = self.transcript(assistant(1000, long=10), assistant(2000, short=10))

        self.assertEqual(idlehandoff.usage_of(path).ttl, 300)

    def test_書き込みが1時間側なら保持時間は1時間(self):
        path = self.transcript(assistant(2000, long=10))

        self.assertEqual(idlehandoff.usage_of(path).ttl, 3600)

    def test_直近が読み取りだけなら遡って書き込みの種類を見る(self):
        path = self.transcript(assistant(1000, short=10), assistant(2000))

        self.assertEqual(idlehandoff.usage_of(path).ttl, 300)

    def test_読めないトランスクリプトは大きさを返さない(self):
        self.assertIsNone(idlehandoff.usage_of(os.path.join(self.dir, "none.jsonl")))
        self.assertIsNone(idlehandoff.usage_of(""))
        self.assertIsNone(idlehandoff.usage_of(self.transcript("not json", "{}")))


class SettingTest(unittest.TestCase):
    def test_閾値の読み方(self):
        self.assertEqual(idlehandoff.threshold(""), 400000)
        self.assertEqual(idlehandoff.threshold("123000"), 123000)
        self.assertEqual(idlehandoff.threshold("0"), 0)
        self.assertEqual(idlehandoff.threshold("-5"), 0)
        self.assertEqual(idlehandoff.threshold("abc"), 400000)

    def test_待ち時間はキャッシュの保持時間の少し手前(self):
        self.assertEqual(idlehandoff.wait_seconds(3600), 55 * 60)
        self.assertEqual(idlehandoff.wait_seconds(300), 4 * 60)

    def test_待ち時間は確かめ用に上書きできる(self):
        self.assertEqual(idlehandoff.wait_seconds(3600, "2"), 2)
        self.assertEqual(idlehandoff.wait_seconds(3600, "x"), 55 * 60)

    def test_見ている人がいない実行を見分ける(self):
        self.assertTrue(idlehandoff.unattended({"CLAUDE_CODE_SESSION_ATTENDED": "0"}))
        self.assertTrue(idlehandoff.unattended({"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}))
        self.assertFalse(idlehandoff.unattended({"CLAUDE_CODE_ENTRYPOINT": "cli"}))
        self.assertFalse(idlehandoff.unattended({}))


class FlowTest(Fixture):
    big = idlehandoff.Usage(tokens=500_000, ttl=3600)

    def begin(self, usage=None, limit=400_000):
        return idlehandoff.start(self.state, SESSION, usage or self.big, limit)

    def test_閾値に届かなければ始めない(self):
        small = idlehandoff.Usage(tokens=399_999, ttl=3600)

        self.assertIsNone(self.begin(small))

    def test_閾値以上なら待機の指示を返す(self):
        plan = self.begin()

        self.assertEqual(plan.wait, 55 * 60)
        self.assertTrue(plan.token)

    def test_待ったあと入力が無ければ起こす指示を返す(self):
        plan = self.begin()

        text = idlehandoff.check(self.state, SESSION, plan.token, plan.wait)

        self.assertIn(idlehandoff.MARKER, text)
        self.assertIn("scratchpad/", text)

    def test_待っている間にユーザが打てば起こさない(self):
        plan = self.begin()

        idlehandoff.reset(self.state, SESSION, "続きをお願いします")

        self.assertIsNone(idlehandoff.check(self.state, SESSION, plan.token, plan.wait))

    def test_起こすときの指示ではユーザが打ったことにしない(self):
        plan = self.begin()

        idlehandoff.reset(self.state, SESSION, idlehandoff.MARKER + " 書いてください")

        self.assertIsNotNone(idlehandoff.check(self.state, SESSION, plan.token, plan.wait))

    def test_バックグラウンド作業の完了通知ではユーザが打ったことにしない(self):
        plan = self.begin()

        idlehandoff.reset(self.state, SESSION, "<task-notification>", background=True)

        self.assertIsNotNone(idlehandoff.check(self.state, SESSION, plan.token, plan.wait))

    def test_古い待機は新しい待機に置き換わると起こさない(self):
        old = self.begin()
        new = self.begin()

        self.assertIsNone(idlehandoff.check(self.state, SESSION, old.token, old.wait))
        self.assertIsNotNone(idlehandoff.check(self.state, SESSION, new.token, new.wait))

    def test_起こしたあとはユーザが打つまで待機を始めない(self):
        plan = self.begin()
        idlehandoff.check(self.state, SESSION, plan.token, plan.wait)

        self.assertIsNone(self.begin())

        idlehandoff.reset(self.state, SESSION, "続けます")
        grown = idlehandoff.Usage(tokens=900_000, ttl=3600)
        self.assertIsNotNone(self.begin(grown))

    def test_起こしたあとは閾値のぶん育つまで促さない(self):
        plan = self.begin()
        idlehandoff.check(self.state, SESSION, plan.token, plan.wait)
        idlehandoff.reset(self.state, SESSION, "続けます")

        near = idlehandoff.Usage(tokens=899_999, ttl=3600)
        far = idlehandoff.Usage(tokens=900_000, ttl=3600)

        self.assertIsNone(self.begin(near))
        self.assertIsNotNone(self.begin(far))

    def test_会話が基準より小さくなったら基準を戻す(self):
        plan = self.begin()
        idlehandoff.check(self.state, SESSION, plan.token, plan.wait)
        idlehandoff.reset(self.state, SESSION, "続けます")

        # 基準は 500,000。会話が 100,000 に縮んだ（/compact のあと）なら基準は 0 に戻り、
        # 次に 400,000 以上に育てば、また促す。
        small = idlehandoff.Usage(tokens=100_000, ttl=3600)
        regrown = idlehandoff.Usage(tokens=450_000, ttl=3600)

        self.assertIsNone(self.begin(small))
        self.assertIsNotNone(self.begin(regrown))

    def test_使えない識別子では何もしない(self):
        self.assertIsNone(idlehandoff.start(self.state, "../x", self.big, 400_000))
        self.assertIsNone(idlehandoff.start(self.state, "", self.big, 400_000))

    def test_0なら無効(self):
        self.assertIsNone(self.begin(limit=0))


class NoticeTest(Fixture):
    big = idlehandoff.Usage(tokens=500_000, ttl=3600)

    def test_知らせは1セッションに1回(self):
        first = idlehandoff.notice(self.state, SESSION, self.big, 400_000)
        second = idlehandoff.notice(self.state, SESSION, self.big, 400_000)

        self.assertTrue(first)
        self.assertIsNone(second)

    def test_知らせは料金の事情を知らない人にも分かる言葉で書く(self):
        text = idlehandoff.notice(self.state, SESSION, self.big, 400_000)

        for word in ("キャッシュ", "料金", "引き継ぎ", "60 分", "scratchpad/"):
            self.assertIn(word, text)
        self.assertIn("500,000", text)

    def test_閾値に届かなければ知らせない(self):
        small = idlehandoff.Usage(tokens=1000, ttl=3600)

        self.assertIsNone(idlehandoff.notice(self.state, SESSION, small, 400_000))


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

    def test_startは待機の3行を返す(self):
        result = self.idle("start", self.payload())

        self.assertEqual(result.returncode, 0, result.stderr)
        lines = dict(line.split(" ", 1) for line in result.stdout.splitlines())
        self.assertEqual(lines["wait"], "7")
        self.assertEqual(lines["session"], SESSION)
        self.assertTrue(lines["token"])

    def test_startとcheckで起こす指示が返る(self):
        started = dict(
            line.split(" ", 1) for line in self.idle("start", self.payload()).stdout.splitlines()
        )

        result = self.idle("check", "", None, SESSION, started["token"], "7")

        self.assertTrue(result.stdout.startswith("fire\n"), result.stdout)
        self.assertIn(idlehandoff.MARKER, result.stdout)

    def test_resetのあとのcheckは起こさない(self):
        started = dict(
            line.split(" ", 1) for line in self.idle("start", self.payload()).stdout.splitlines()
        )

        self.idle("reset", self.payload("UserPromptSubmit", prompt="続けます"))
        result = self.idle("check", "", None, SESSION, started["token"], "7")

        self.assertTrue(result.stdout.startswith("skip"), result.stdout)

    def test_サブエージェントと見ている人がいない実行は始めない(self):
        sub = self.idle("start", self.payload(agent_id="agent-1"))
        sdk = self.idle("start", self.payload(), {"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"})
        headless = self.idle("start", self.payload(), {"CLAUDE_CODE_SESSION_ATTENDED": "0"})

        self.assertEqual(sub.stdout, "skip subagent\n")
        self.assertEqual(sdk.stdout, "skip unattended\n")
        self.assertEqual(headless.stdout, "skip unattended\n")

    def test_環境変数を0にすると無効(self):
        result = self.idle("start", self.payload(), {"CCNAVI_HANDOFF_SUGGEST_TOKENS": "0"})

        self.assertEqual(result.stdout, "skip disabled\n")

    def test_環境変数で閾値を変えられる(self):
        result = self.idle("start", self.payload(), {"CCNAVI_HANDOFF_SUGGEST_TOKENS": "600000"})

        self.assertEqual(result.stdout, "skip not-due\n")

    def test_noticeはsystemMessageのJSONを1回だけ出す(self):
        first = self.idle("notice", self.payload())
        second = self.idle("notice", self.payload())

        self.assertIn("systemMessage", json.loads(first.stdout))
        self.assertEqual(second.stdout, "")

    def test_payloadが無くても止まらない(self):
        for verb in ("notice", "start", "reset"):
            with self.subTest(verb=verb):
                self.assertEqual(self.idle(verb, "").returncode, 0)


if __name__ == "__main__":
    unittest.main()
