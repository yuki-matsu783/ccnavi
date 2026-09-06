"""ルールが言及しない呼び出しの結末が、Claude Code の権限モードで変わること。

外から道具を動かし、読み返すのは標準出力と記録の 1 行だけ。ccnavi が
「判定を返さない」ことは、返した JSON の有無でしか外から見えないので、
そこを直接見る。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(ROOT, "testdata", "rules-undeclared.yml")

# どのルールも言及しない呼び出し。allow にも deny にも ask にも当たらない。
UNDECLARED_COMMAND = "docker run --rm alpine"


def run(permission_mode, command=UNDECLARED_COMMAND, mode="enable"):
    """道具を 1 回動かし、標準出力と記録の 1 行を返す。"""
    environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
    payload = json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "permission_mode": permission_mode,
        }
    )

    with tempfile.TemporaryDirectory(prefix="ccnavi-mode-") as directory:
        log = os.path.join(directory, "log.jsonl")
        result = subprocess.run(
            [sys.executable, "-m", "ccnavi", "--rules", RULES, "--mode", mode, "--log", log],
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=ROOT,
            env=environment,
        )
        with open(log, encoding="utf-8") as handle:
            record = json.loads(handle.read().splitlines()[-1])

    return result, record


def decision_of(case, result):
    """応答に載った判定。判定を返していなければ None。"""
    if not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)["hookSpecificOutput"].get("permissionDecision")
    except (ValueError, KeyError) as exc:
        case.fail(f"標準出力が期待した JSON ではない: {exc}\nstdout: {result.stdout!r}")


class HandoverTest(unittest.TestCase):
    def test_auto_gets_no_verdict(self):
        """auto では判定を返さない。classifier がそのために居る。"""
        result, record = run("auto")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIsNone(decision_of(self, result), "判定を返している")

    def test_the_handover_is_still_recorded(self):
        """渡した回も記録に残る。ここが唯一「ルールが薄い場所」の手掛かりになる。"""
        _, record = run("auto")
        self.assertEqual("handover", record["decision"])
        self.assertEqual("UNDECLARED", record["code"])
        self.assertEqual("auto", record["permission_mode"])
        self.assertFalse(record["enforced"])

    def test_handover_is_not_counted_as_asking_the_user(self):
        """渡した回と人に聞いた回は、記録の上で混ざらない。"""
        _, handed = run("auto")
        _, asked = run("default")
        self.assertNotEqual(handed["decision"], asked["decision"])


class AskTest(unittest.TestCase):
    def test_default_still_asks(self):
        """人が居るモードは今までどおり確認に出す。"""
        result, record = run("default")
        self.assertEqual("ask", decision_of(self, result))
        self.assertEqual("ask", record["decision"])
        self.assertEqual("UNDECLARED", record["code"])

    def test_an_unknown_mode_asks(self):
        """知らないモードは確認に倒す。名前が 1 つ増えても素通りにしない。"""
        result, _ = run("someFutureMode")
        self.assertEqual("ask", decision_of(self, result))

    def test_a_missing_mode_asks(self):
        """モードが payload に無くても確認に倒す。"""
        result, _ = run("")
        self.assertEqual("ask", decision_of(self, result))

    def test_an_unreadable_command_is_never_handed_over(self):
        """読み切れなかったコマンドは渡さない。読めなかったことは委ねた先に伝わらない。"""
        result, record = run("auto", command="bash -c 'docker run --rm alpine'")
        self.assertEqual("ask", decision_of(self, result))
        self.assertEqual("PARSE_UNCERTAIN", record["code"])


class NoJudgeTest(unittest.TestCase):
    def test_bypass_permissions_refuses(self):
        """確認できる者が居ないモードでは許可としない（REQ-PRE-08）。"""
        result, record = run("bypassPermissions")
        self.assertEqual("deny", decision_of(self, result))
        self.assertEqual("deny", record["decision"])
        self.assertEqual("UNDECLARED", record["code"])

    def test_dont_ask_refuses(self):
        result, _ = run("dontAsk")
        self.assertEqual("deny", decision_of(self, result))

    def test_the_refusal_says_asking_is_not_available(self):
        """断りの文面は、言えば通るかもしれない ask とは別の次の一手を示す。"""
        result, _ = run("bypassPermissions")
        reason = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("no one to ask", reason)
        self.assertNotIn("asks the user", reason)


class RulesStillWinTest(unittest.TestCase):
    """権限モードが効くのは、ルールが何も言わなかったときだけ。"""

    def test_deny_holds_in_every_mode(self):
        for permission_mode in ("auto", "default", "bypassPermissions", "dontAsk"):
            with self.subTest(permission_mode=permission_mode):
                result, _ = run(permission_mode, command="git push origin main")
                self.assertEqual("deny", decision_of(self, result))

    def test_an_explicit_ask_is_never_handed_over(self):
        """ルールが ask と書いた場所は、auto でも人に出す。意図した確認ポイント。"""
        result, record = run("auto", command="git commit -m x")
        self.assertEqual("ask", decision_of(self, result))
        self.assertEqual("RULE_ASK", record["code"])

    def test_allow_holds_in_every_mode(self):
        for permission_mode in ("auto", "bypassPermissions"):
            with self.subTest(permission_mode=permission_mode):
                result, record = run(permission_mode, command="ls -la")
                self.assertIsNone(decision_of(self, result))
                self.assertEqual("allow", record["decision"])


class DryRunTest(unittest.TestCase):
    def test_dry_run_says_nothing_about_a_handover(self):
        """enable が何もしない回は、dry-run も何も言わない。報告が実物と一致する。"""
        result, record = run("auto", mode="dry-run")
        self.assertEqual("", result.stdout.strip())
        self.assertEqual("handover", record["decision"])

    def test_dry_run_still_reports_what_enable_would_refuse(self):
        result, _ = run("bypassPermissions", mode="dry-run")
        self.assertIn("would have stopped", result.stdout)


if __name__ == "__main__":
    unittest.main()
