"""ユーザだけが打つ実行ファイルの形を、hook の入口から確かめる
（src/ccnavi/tickets/phase_forms.py）。

1. 状態の操作（`ticket finish` など）は、副命令の前に `--` を挟んでも止まる。挟んだ形に
   `--record-tree` を足すと、実行ファイルの「write-and-push を通っていない」検査も外れる
2. 親を閉じたときの片付け（`sync tidy`・`worktree retire`・`worktree branch`・
   `ccnavi-clean.sh --branch` / `--confirmed`）は、メインのエージェントもサブエージェントも
   直には打てない。呼ぶのは sh の中（hook を通らない）か、ユーザが `!` を付けて打つときだけ
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


class CleanupFormsTest(HookCase):
    STOPPED = (
        "ccnavi sync tidy i0001 self i0001 0123456789abcdef",
        "ccnavi --cwd /x sync -- tidy i0001 self i0001 0123456789abcdef",
        "python -m ccnavi worktree retire i0001",
        "uv run python -m ccnavi worktree retire i0001",
        ".ccnavi/bin/ccnavi worktree branch i0001-01-01",
        "ccnavi --root /x worktree branch i0001-01-01 confirmed",
        "sh .ccnavi/scripts/ccnavi-clean.sh --branch i0001-01-01 --confirmed",
        "sh .ccnavi/scripts/ccnavi-clean.sh --branch i0001-01-01",
        "bash -x .ccnavi/scripts/ccnavi-clean.sh --confirmed --branch i0001-01-01",
        "cd /x && sh .ccnavi/scripts/ccnavi-clean.sh --branch i0001-01-01 --confirmed",
    )
    PASSED = (
        # 生成物とワークツリーの片付け（ブランチは消さない）と、片付けを中で呼ぶ sh。
        "sh .ccnavi/scripts/ccnavi-clean.sh --worktree i0001-01-01",
        "sh .ccnavi/scripts/ccnavi-clean.sh i0001-01-01 --dry-run",
        "sh .ccnavi/scripts/ccnavi-sync.sh i0001",
        "sh .ccnavi/scripts/ccnavi-sync.sh",
        "ccnavi worktree drop i0001-01-01",
        "ccnavi worktree remaining i0001",
    )

    def test_the_agents_cannot_type_the_cleanup(self):
        for command in self.STOPPED:
            for agent in ("", "a1"):
                with self.subTest(command=command, agent=agent):
                    decision, reason = self.hook(command, agent)
                    self.assertEqual(decision, "deny", reason)
                    if agent and "ccnavi-clean.sh" in command:
                        # clean の sh は、サブエージェントには役割の表で先に止まる。
                        self.assertIn("DENY_SUBAGENT_TICKET_OP", reason)
                        continue
                    self.assertIn(phase_forms.CODE_TICKET_APPROVAL, reason)
                    self.assertIn("! を付けて", reason)

    def test_the_scripts_that_call_it_are_not_stopped_by_it(self):
        # sh の中から実行ファイルを呼ぶ分は hook を通らない。sh を打つ形はこの止めに当たらない。
        for command in self.PASSED:
            with self.subTest(command=command):
                _, reason = self.hook(command)
                self.assertNotIn(phase_forms.CODE_TICKET_APPROVAL, reason)


if __name__ == "__main__":
    unittest.main()
