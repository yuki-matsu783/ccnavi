"""ターン分離の関門を、hook の入口から実際の呼び出しの順で確かめる
（`integrate` → `integrate-done`）。

UserPromptSubmit・PreToolUse・PostToolUseFailure・Stop・SessionStart を続けて呼ぶ。

見るのは次の 8 つ。

- 同じ発言の中で `integrate` → `integrate-done` は止まる。発言を 1 回はさむと通る
- バックグラウンド作業の完了通知は、何回挟んでも開かない。完了通知のあとにユーザの発言が来れば開く
- Stop の促しで続けたターン（Stop）も、ユーザの発言に数えない
- キーが違う名前、`--abort`、`integrate` の失敗・拒否は対象外
- サブエージェント、別のセッション、並行した呼び出し、同じコマンドの連結
- 起動と再開は記録を消す。compact は消さない。dry-run でも止まる
- 止めた記録（`decisions.jsonl`）の拒否コードと `rules`
- 壊れた関門の記録は止め、次のユーザの発言で消える（完了通知では消えない）
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from ccnavi.hook import turngate
from tests import ROOT
from tests.inproc import run_ccnavi

GIT = "sh .ccnavi/scripts/ccnavi-git.sh"
INTEGRATE = f"{GIT} integrate foo"
DONE = f"{GIT} integrate-done foo -m 'msg x'"
NOTICE = "<task-notification>\n<status>completed</status>\n</task-notification>"


class TurnGateHookTest(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="ccnavi-turngate-hook-")
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.state = os.path.join(self.repo, "logs", "state")
        os.makedirs(os.path.join(self.repo, ".ccnavi", "config", "common"))
        self.calls = 0

    def hook(self, event, session="s1", mode="enable", **body):
        payload = {"hook_event_name": event, "session_id": session, "tool_name": "Bash"}
        payload["tool_input"] = {"command": "ls"}
        payload.update(body)
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment["CCNAVI_LEARNING"] = "disable"
        environment["CCNAVI_MODE"] = mode
        return run_ccnavi(
            ["--root", self.repo], input=json.dumps(payload), cwd=ROOT, env=environment
        )

    def say(self, text="お願いします", **kw):
        return self.hook("UserPromptSubmit", prompt=text, **kw)

    def run_command(self, command, **kw):
        """PreToolUse で 1 回打つ。"""
        self.calls += 1
        kw.setdefault("tool_use_id", f"toolu_{self.calls}")
        return self.hook("PreToolUse", tool_input={"command": command}, **kw)

    def stopped(self, done):
        return turngate.CODE in done.stdout

    def entries(self):
        if not os.path.isdir(self.state):
            return []
        return sorted(n for n in os.listdir(self.state) if n.startswith("turngate-"))

    def decisions(self):
        path = os.path.join(self.repo, "logs", "decisions.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]


class FlowTest(TurnGateHookTest):
    def test_同じ発言の中の_integrate_のあとの_integrate_done_は止まる(self):
        self.say()
        self.assertFalse(self.stopped(self.run_command(INTEGRATE)))

        done = self.run_command(DONE)

        self.assertTrue(self.stopped(done))
        self.assertIn('"permissionDecision": "deny"', done.stdout)
        self.assertIn("builtin:turn-gate:integrate-user-turn", done.stdout)
        self.assertIn("キー foo", done.stdout)

    def test_ユーザの発言を_1_回はさむと通る(self):
        self.say()
        self.run_command(INTEGRATE)
        self.hook("Stop")

        self.say("差分を見ました。取り込んでください")

        self.assertFalse(self.stopped(self.run_command(DONE)))

    def test_通ったあとは記録が消える(self):
        self.say()
        self.run_command(INTEGRATE)
        self.say("どうぞ")
        self.run_command(DONE)

        self.assertEqual(self.entries(), [])
        self.assertFalse(self.stopped(self.run_command(DONE)))

    def test_止めた記録は拒否コードと宣言を残す(self):
        self.say()
        self.run_command(INTEGRATE)
        self.run_command(DONE)

        denied = [d for d in self.decisions() if d.get("decision") == "deny"]

        self.assertEqual(len(denied), 1)
        self.assertEqual(denied[0]["code"], "DENY_NEEDS_USER_TURN")
        self.assertEqual(denied[0]["rules"], ["builtin:turn-gate:integrate-user-turn"])


class BrokenEntryTest(TurnGateHookTest):
    def test_壊れた記録は止め_次のユーザの発言で解ける(self):
        self.say()
        self.run_command(INTEGRATE)
        path = os.path.join(self.state, self.entries()[0])
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not json")

        done = self.run_command(DONE)
        self.assertTrue(self.stopped(done))
        self.assertIn(self.entries()[0], done.stdout)

        self.say(NOTICE)
        self.assertTrue(self.stopped(self.run_command(DONE)))

        self.say("どうぞ")
        self.assertEqual(self.entries(), [])
        self.assertFalse(self.stopped(self.run_command(DONE)))


class NotUserTurnTest(TurnGateHookTest):
    def test_完了通知のあとのターンでも止まる(self):
        self.say()
        self.run_command(INTEGRATE)
        self.hook("Stop")

        self.say(NOTICE)

        self.assertTrue(self.stopped(self.run_command(DONE)))

    def test_完了通知を何回はさんでも開かない(self):
        self.say()
        self.run_command(INTEGRATE)
        for _ in range(3):
            self.hook("Stop")
            self.say(NOTICE)
            self.assertTrue(self.stopped(self.run_command(DONE)))

    def test_完了通知のあとにユーザの発言が来れば開く(self):
        self.say()
        self.run_command(INTEGRATE)
        self.hook("Stop")
        self.say(NOTICE)
        self.assertTrue(self.stopped(self.run_command(DONE)))

        self.hook("Stop")
        self.say("確認しました")

        self.assertFalse(self.stopped(self.run_command(DONE)))

    def test_ユーザの発言の判定の形ごとに_数える_数えない(self):
        # 数えない形は、そのあとも止まったまま。数える形は、開く。
        not_counted = (
            NOTICE,
            "経過です。<task-notification> が終わりました",
            "[idle-handoff] 引き継ぎを書いてください",
            '<wake reason="external-event"><event source="github"></event></wake>',
            "<webhook-payload>{}</webhook-payload>",
            "<child-session-event>failed</child-session-event>",
            "[SYSTEM NOTIFICATION] ビルドが終わりました",
            "<system-reminder>メモ</system-reminder>",
            "",
        )
        for text in not_counted:
            with self.subTest(not_counted=text):
                self.hook("SessionStart", source="startup")
                self.say()
                self.run_command(INTEGRATE)
                self.hook("Stop")
                self.say(text)
                self.assertTrue(self.stopped(self.run_command(DONE)))
        for text in ("取り込んでください", "ok", "予約した実行の文のように見える文"):
            with self.subTest(counted=text):
                self.hook("SessionStart", source="startup")
                self.say()
                self.run_command(INTEGRATE)
                self.hook("Stop")
                self.say(text)
                self.assertFalse(self.stopped(self.run_command(DONE)))

    def test_Stop_の促しで同じターンを続けても開かない(self):
        self.say()
        self.run_command(INTEGRATE)
        # Stop が decision: block で続けさせたターンは、新しい UserPromptSubmit を起こさない。
        self.hook("Stop")

        self.assertTrue(self.stopped(self.run_command(DONE)))

    def test_動かしている作業が残っていてもユーザの返事で開く(self):
        self.say()
        self.run_command(INTEGRATE)
        self.hook("Stop", background_tasks=[{"id": "b1"}])

        self.say("確認しました")

        self.assertFalse(self.stopped(self.run_command(DONE)))


class ScopeTest(TurnGateHookTest):
    def test_キーが違う名前は対象外(self):
        self.say()
        self.run_command(INTEGRATE)

        other = f"{GIT} integrate-done bar -m x"

        self.assertFalse(self.stopped(self.run_command(other)))
        self.assertTrue(self.stopped(self.run_command(DONE)))

    def test_メッセージが先でも名前で組む(self):
        self.say()
        self.run_command(INTEGRATE)

        self.assertTrue(self.stopped(self.run_command(f"{GIT} integrate-done -m 'msg x' foo")))
        self.assertFalse(self.stopped(self.run_command(f"{GIT} integrate-done -m foo bar")))

    def test_integrate_の_abort_は_A_にならない(self):
        self.say()
        self.run_command(f"{GIT} integrate --abort foo")

        self.assertFalse(self.stopped(self.run_command(DONE)))

    def test_integrate_がなければ_integrate_done_は対象外(self):
        self.say()

        self.assertFalse(self.stopped(self.run_command(DONE)))

    def test_失敗した_integrate_は数えない(self):
        self.say()
        self.run_command(INTEGRATE, tool_use_id="toolu_a")
        self.hook("PostToolUseFailure", tool_use_id="toolu_a", error="rejected")

        self.assertFalse(self.stopped(self.run_command(DONE)))

    def test_失敗した_integrate_は_前の_integrate_の返事を無にしない(self):
        self.say()
        self.run_command(INTEGRATE)
        self.hook("Stop")
        self.say("差分を見ました")
        # 返事のあとに、誤って integrate をもう 1 度打ったが失敗した。
        self.run_command(INTEGRATE, tool_use_id="toolu_b")
        self.hook("PostToolUseFailure", tool_use_id="toolu_b", error="rejected")

        self.assertFalse(self.stopped(self.run_command(DONE)))

    def test_拒否された_integrate_は数えない(self):
        # ルールの deny で止めた呼び出しは走らない。
        rules = os.path.join(self.repo, ".ccnavi", "config", "common", "rules.yml")
        with open(rules, "w", encoding="utf-8") as f:
            f.write(
                "version: 1\n"
                "deny:\n"
                "  - id: no-integrate\n"
                "    match: Bash\n"
                "    glob: '*integrate foo*'\n"
                "    message: 止めます。\n"
            )
        self.say()
        denied = self.run_command(INTEGRATE)
        self.assertIn("no-integrate", denied.stdout)

        self.assertFalse(self.stopped(self.run_command(DONE)))

    def test_ユーザの端末で打つ道には働かない(self):
        # ccnavi-user-*.sh の入口は、組み込みの宣言のどちらの条件にもヒットしない。
        self.say()
        self.run_command(INTEGRATE)

        done = self.run_command("sh .ccnavi/scripts/ccnavi-user-handoff.sh")

        self.assertFalse(self.stopped(done))


class WhoTest(TurnGateHookTest):
    def test_サブエージェントが打っても止まる(self):
        self.say()
        self.run_command(INTEGRATE)

        self.assertTrue(self.stopped(self.run_command(DONE, agent_id="sub1", agent_type="x")))

    def test_サブエージェントの_integrate_は別の関門が止める_止めた_A_は数えない(self):
        self.say()
        denied = self.run_command(INTEGRATE, agent_id="sub1", agent_type="x")
        self.assertIn("DENY_SUBAGENT_TICKET_OP", denied.stdout)

        self.assertFalse(self.stopped(self.run_command(DONE)))

    def test_別のセッションの_integrate_は数えない(self):
        self.say(session="s1")
        self.run_command(INTEGRATE, session="s1")
        self.say(session="s2")

        self.assertFalse(self.stopped(self.run_command(DONE, session="s2")))
        self.assertTrue(self.stopped(self.run_command(DONE, session="s1")))

    def test_並行した呼び出しでも_実行前の記録で止まる(self):
        # 同じメッセージで A と B を並べると、A の実行後を待たずに B の実行前が来る。
        self.say()
        self.run_command(INTEGRATE)

        self.assertTrue(self.stopped(self.run_command(DONE)))

    def test_同じコマンドに連結しても止まる(self):
        self.say()

        done = self.run_command(f"{INTEGRATE} && {DONE}")

        self.assertTrue(self.stopped(done))
        self.assertIn("1 つのコマンドにまとめて", done.stdout)

    def test_実行役の中でも止まる(self):
        self.say()
        self.run_command(f"env X=1 {INTEGRATE}")

        self.assertTrue(self.stopped(self.run_command(f'sh -c "{DONE}"')))

    def test_変数で名前を渡しても止まる(self):
        self.say()
        self.run_command(INTEGRATE)

        self.assertTrue(self.stopped(self.run_command(f"{GIT} integrate-done $name -m x")))


class SessionTest(TurnGateHookTest):
    def test_再開で記録を破棄する(self):
        self.say()
        self.run_command(INTEGRATE)

        self.hook("SessionStart", source="resume")

        self.assertEqual(self.entries(), [])
        self.assertFalse(self.stopped(self.run_command(DONE)))

    def test_起動で記録を破棄する(self):
        self.say()
        self.run_command(INTEGRATE)

        self.hook("SessionStart", source="startup")

        self.assertEqual(self.entries(), [])

    def test_compact_では破棄しない(self):
        # ターンの途中で compact が走りうる。
        self.say()
        self.run_command(INTEGRATE)

        self.hook("SessionStart", source="compact")

        self.assertTrue(self.stopped(self.run_command(DONE)))

    def test_再開のあとは_ユーザの発言の記録も消える(self):
        self.say()
        self.hook("SessionStart", source="resume")

        self.assertFalse(os.path.exists(os.path.join(self.state, "userprompt-s1.json")))

    def test_dry_run_でも止まる(self):
        self.say(mode="dry-run")
        self.run_command(INTEGRATE, mode="dry-run")

        done = self.run_command(DONE, mode="dry-run")

        self.assertTrue(self.stopped(done))
        self.assertIn("this stop still applies", done.stdout)

    def test_判定を切っているモードでは働かない(self):
        self.say(mode="disable")
        self.run_command(INTEGRATE, mode="disable")

        self.assertFalse(self.stopped(self.run_command(DONE, mode="disable")))

    def test_記録の置き場を読めなくても内部エラーにしない(self):
        os.makedirs(os.path.dirname(self.state), exist_ok=True)
        with open(self.state, "w", encoding="utf-8") as f:
            f.write("x")

        done = self.run_command(DONE)

        self.assertNotIn("internal-error", done.stderr)


if __name__ == "__main__":
    unittest.main()
