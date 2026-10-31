"""失敗や拒否が続いたとき、資料を引く案内を出すこと（issue #270）。

外から道具を動かし、読み返すのは応答の JSON と state の置き場だけ。判定そのもの（deny）は変わらず、
連続が閾値（CCNAVI_FAIL_STREAK_COUNT、既定 3）の倍数になった回に、`--docs` で索引を引く案内が付く。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from ccnavi.records import failstreak
from tests import ROOT, common_path
from tests.inproc import run_ccnavi

FIXTURE = "rules-undeclared.yml"
NOTE = "失敗か拒否が"
FIRST = "失敗したか、ccnavi に拒否されました"
DOCS = "--docs"


def _env(**extra: str) -> dict[str, str]:
    environment = {
        **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
        "CCNAVI_LEARNING": "disable",
    }
    # コアファイルのバックアップと復元は切る。試したいのは連続の数えだけ。
    environment["CCNAVI_GUARD_CORE_FILES"] = "disable"
    environment.update(extra)
    return environment


class ThresholdTest(unittest.TestCase):
    def test_default_zero_and_bad_values(self):
        self.assertEqual(3, failstreak.threshold(""))
        self.assertEqual(5, failstreak.threshold(" 5 "))
        self.assertEqual(0, failstreak.threshold("0"))
        self.assertEqual(3, failstreak.threshold("-1"))
        self.assertEqual(3, failstreak.threshold("abc"))
        self.assertEqual(3, failstreak.threshold("2.5"))


class FailStreakTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory(prefix="ccnavi-streak-")
        self.ws = self._dir.name
        self.state = os.path.join(self.ws, "logs", "state")
        target = common_path(self.ws, "rules")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(os.path.join(ROOT, "tests", "fixtures", FIXTURE), target)

    def tearDown(self):
        self._dir.cleanup()

    def _reset_state(self):
        shutil.rmtree(self.state, ignore_errors=True)

    def _run(self, payload: dict, mode: str = "enable", session: str = "s1", **env: str):
        body = {"permission_mode": "auto", "cwd": self.ws, **payload}
        if session:
            body["session_id"] = session
        return run_ccnavi(
            ["--root", self.ws, "--mode", mode],
            input=json.dumps(body),
            env=_env(**env),
            cwd=self.ws,
        )

    def _fail(self, **kw):
        agent = kw.pop("agent", "")
        interrupt = kw.pop("interrupt", False)
        payload = {
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Bash",
            "tool_input": {"command": "false"},
            "error": "Exit code 1",
            "is_interrupt": interrupt,
        }
        if agent:
            payload["agent_id"] = agent
        return self._run(payload, **kw)

    def _ok(self, **kw):
        return self._run(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "true"},
                "tool_response": {},
            },
            **kw,
        )

    def _deny(self, **kw):
        return self._run(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "git push origin main"},
            },
            **kw,
        )

    def _context(self, result) -> str:
        """応答の文。無ければ空。"""
        if not result.stdout.strip():
            return ""
        out = json.loads(result.stdout)["hookSpecificOutput"]
        return out.get("permissionDecisionReason", "") or out.get("additionalContext", "")

    def _files(self) -> list[str]:
        names = os.listdir(self.state) if os.path.isdir(self.state) else []
        return [n for n in names if n.startswith("failstreak-")]

    def test_note_comes_every_threshold_failures(self):
        said = []
        for _ in range(7):
            result = self._fail()
            self.assertEqual(0, result.returncode, result.stderr)
            said.append(self._context(result))
        self.assertIn(FIRST, said[0])
        self.assertNotIn(NOTE, said[0])
        self.assertEqual("", said[1])
        self.assertIn(NOTE, said[2])
        self.assertIn("3 回続いて", said[2])
        self.assertIn(DOCS, said[2])
        self.assertEqual(["", ""], said[3:5])
        self.assertIn("6 回続いて", said[5])
        self.assertEqual("", said[6])

    def test_note_event_name_is_the_failure_event(self):
        out = json.loads(self._fail().stdout)["hookSpecificOutput"]
        self.assertEqual("PostToolUseFailure", out["hookEventName"])

    def test_note_does_not_urge_bypass(self):
        for text in (
            self._context(self._fail()),
            self._context(self._fail()),
            self._context(self._fail()),
        ):
            if text:
                self.assertIn("迂回", text)
                self.assertIn("ユーザに相談", text)

    def test_success_resets_the_count_but_keeps_the_first_mark(self):
        self.assertIn(FIRST, self._context(self._fail()))
        self._fail()
        self._ok()
        # 連続は 0 に戻り、最初の 1 回を案内した印だけが残る。
        [name] = self._files()
        with open(os.path.join(self.state, name), encoding="utf-8") as f:
            self.assertEqual({"first": True}, json.load(f))
        self.assertEqual("", self._context(self._fail()))
        self.assertEqual("", self._context(self._fail()))
        self.assertIn(NOTE, self._context(self._fail()))
        # 案内のあとに成功が入れば、数え直してまた 3 回目で案内する。最初の 1 回の案内は出ない。
        self._ok()
        said = [self._context(self._fail()) for _ in range(3)]
        self.assertEqual(["", ""], said[:2])
        self.assertIn(NOTE, said[2])

    def test_success_without_a_streak_writes_nothing(self):
        self._ok()
        self.assertEqual([], self._files())

    def test_zero_disables_counting(self):
        for _ in range(5):
            result = self._fail(CCNAVI_FAIL_STREAK_COUNT="0")
            self.assertEqual("", self._context(result))
        self.assertEqual([], self._files())
        for _ in range(5):
            self.assertNotIn(NOTE, self._context(self._deny(CCNAVI_FAIL_STREAK_COUNT="0")))
        self.assertEqual([], self._files())

    def test_bad_values_fall_back_to_the_default(self):
        for bad in ("abc", "-2"):
            self._reset_state()
            said = [self._context(self._fail(CCNAVI_FAIL_STREAK_COUNT=bad)) for _ in range(3)]
            self.assertIn(FIRST, said[0], bad)
            self.assertEqual("", said[1], bad)
            self.assertIn(NOTE, said[2], bad)

    def test_threshold_follows_the_environment(self):
        self.assertIn(FIRST, self._context(self._fail(CCNAVI_FAIL_STREAK_COUNT="2")))
        self.assertIn(NOTE, self._context(self._fail(CCNAVI_FAIL_STREAK_COUNT="2")))

    def test_deny_is_counted_and_keeps_its_verdict(self):
        """拒否も数える。案内は拒否の文面の末尾に付き、判定は deny のまま。"""
        reasons = []
        for _ in range(3):
            result = self._deny()
            out = json.loads(result.stdout)["hookSpecificOutput"]
            self.assertEqual("deny", out["permissionDecision"])
            reasons.append(out["permissionDecisionReason"])
        self.assertNotIn(NOTE, reasons[0])
        self.assertNotIn(NOTE, reasons[1])
        self.assertIn(NOTE, reasons[2])
        self.assertIn(DOCS, reasons[2])

    def test_deny_is_counted_in_dry_run_too(self):
        said = [self._context(self._deny(mode="dry-run")) for _ in range(3)]
        self.assertNotIn(NOTE, said[0])
        self.assertNotIn(NOTE, said[1])
        self.assertIn(NOTE, said[2])
        self.assertIn("dry-run", said[2])

    def test_failures_and_denies_share_one_streak(self):
        self._fail()
        self._deny()
        self.assertIn(NOTE, self._context(self._fail()))

    def test_call_that_is_not_denied_is_not_counted(self):
        for _ in range(4):
            result = self._run(
                {
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": "ls"},
                }
            )
            self.assertNotIn(NOTE, result.stdout)
        # 数えていないので、最初の失敗はまだ最初の 1 回のまま。
        said = self._context(self._fail())
        self.assertIn(FIRST, said)
        self.assertNotIn(NOTE, said)

    def test_interrupt_is_not_counted(self):
        for _ in range(4):
            self.assertEqual("", self._context(self._fail(interrupt=True)))
        self.assertEqual([], self._files())

    def test_empty_session_is_not_counted(self):
        for _ in range(4):
            self.assertEqual("", self._context(self._fail(session="")))
        self.assertEqual([], self._files())

    def test_sessions_are_counted_apart(self):
        self._fail(session="a")
        self._fail(session="b")
        self._fail(session="a")
        self.assertEqual("", self._context(self._fail(session="b")))
        self.assertIn(NOTE, self._context(self._fail(session="a")))

    def test_subagent_and_main_are_counted_apart(self):
        """サブエージェントの連続はメインの連続と混ぜない。メインの成功は子の連続を消さない。"""
        self._fail(agent="sub1")
        self._fail(agent="sub1")
        self._fail()
        self._ok()
        self.assertEqual("", self._context(self._fail()))
        self.assertIn(NOTE, self._context(self._fail(agent="sub1")))
        self.assertEqual(
            ["failstreak-s1-main.json", "failstreak-s1-sub1.json"], sorted(self._files())
        )

    def test_unwritable_state_gives_no_note_and_no_crash(self):
        os.makedirs(os.path.dirname(self.state), exist_ok=True)
        with open(self.state, "w", encoding="utf-8") as f:
            f.write("x")
        for _ in range(4):
            result = self._fail()
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("", self._context(result))
        decision = json.loads(self._deny().stdout)["hookSpecificOutput"]["permissionDecision"]
        self.assertEqual("deny", decision)


if __name__ == "__main__":
    unittest.main()
