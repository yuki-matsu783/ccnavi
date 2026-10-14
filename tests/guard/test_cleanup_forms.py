"""ユーザだけが打つ実行ファイルの形を、hook の入口から確かめる
（src/ccnavi/tickets/phase_forms.py）。

1. 状態の操作（`ticket finish` など）は、副命令の前に `--` を挟んでも止まる。挟んだ形に
   `--record-tree` を足すと、実行ファイルの「C1 を通っていない」検査も外れる
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from ccnavi.tickets import phase_forms
from tests import ROOT, common_path
from tests.inproc import run_ccnavi


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class HookCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-cleanup-forms-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, ".claude"))
        _write(common_path(self.root, "rules"), json.dumps({"version": 1}))

    def hook(self, command, agent=""):
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "cwd": self.root,
            "session_id": "s1",
            "tool_input": {"command": command},
        }
        if agent:
            payload["agent_id"] = agent
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        env.pop("CLAUDE_PROJECT_DIR", None)
        result = run_ccnavi(
            ["--root", self.root, "--mode", "enable"],
            input=json.dumps(payload),
            cwd=ROOT,
            env=env,
        )
        if not result.stdout.strip():
            return "", ""
        out = json.loads(result.stdout).get("hookSpecificOutput", {})
        return out.get("permissionDecision", ""), out.get("permissionDecisionReason") or ""


class DashesBeforeTheVerbTest(HookCase):
    def test_dashes_before_the_verb_are_read_through(self):
        for command in (
            "ccnavi --record-writes /a --record-tree /b ticket -- finish i0001-01-01",
            "python -m ccnavi ticket -- cancel i0001-01-01",
            "ccnavi review -- ready --result /tmp/r.json",
            "ccnavi ticket --  start i0001",
        ):
            for agent in ("", "a1"):
                with self.subTest(command=command, agent=agent):
                    decision, reason = self.hook(command, agent)
                    self.assertEqual(decision, "deny", reason)
                    self.assertIn(phase_forms.CODE_TICKET_APPROVAL, reason)

    def test_reading_the_ticket_is_not_stopped(self):
        decision, reason = self.hook("ccnavi ticket -- status")
        self.assertNotIn(phase_forms.CODE_TICKET_APPROVAL, reason)


if __name__ == "__main__":
    unittest.main()
