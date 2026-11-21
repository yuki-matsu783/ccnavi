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
        for i in range(7):
            result = self._fail()
            self.assertEqual(0, result.returncode, result.stderr)
            if i == 0:
                out = json.loads(result.stdout)["hookSpecificOutput"]
                self.assertEqual("PostToolUseFailure", out["hookEventName"])
            said.append(self._context(result))
        for text in (said[0], said[2]):
            self.assertIn("迂回", text)
            self.assertIn("ユーザに相談", text)
        self.assertIn(FIRST, said[0])
        self.assertNotIn(NOTE, said[0])
        self.assertEqual("", said[1])
        self.assertIn(NOTE, said[2])
        self.assertIn("3 回続いて", said[2])
        self.assertIn(DOCS, said[2])
        self.assertEqual(["", ""], said[3:5])
        self.assertIn("6 回続いて", said[5])
        self.assertEqual("", said[6])

    def test_artifact_failure_names_the_workspace_scratchpad(self):
        payload = {
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Artifact",
            "tool_input": {"file_path": "/tmp/x.html"},
            "error": "failed",
            "is_interrupt": False,
        }
        said = self._context(self._run(payload))
        self.assertIn("Artifact ツールが失敗しました", said)
        self.assertIn(os.path.join(self.ws, "scratchpad").replace("\\", "/"), said)

    def test_artifact_interrupt_says_nothing_about_scratchpad(self):
        payload = {
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Artifact",
            "tool_input": {"file_path": "/tmp/x.html"},
            "error": "interrupted",
            "is_interrupt": True,
        }
        self.assertNotIn("scratchpad", self._context(self._run(payload)))

    def test_other_tool_failure_does_not_mention_artifact(self):
        self.assertNotIn("Artifact", self._context(self._fail()))

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
        # 数えていないので、最初の 1 回の案内はこの失敗で初めて出る。
        said = self._context(self._fail())
        self.assertIn(FIRST, said)
        self.assertNotIn(NOTE, said)

    def test_interrupt_is_not_counted(self):
        for _ in range(4):
            self.assertEqual("", self._context(self._fail(interrupt=True)))
        self.assertEqual([], self._files())

    def test_sessions_are_counted_apart(self):
        self._fail(session="a")
        self._fail(session="b")
        self._fail(session="a")
        self.assertEqual("", self._context(self._fail(session="b")))
        self.assertIn(NOTE, self._context(self._fail(session="a")))

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
