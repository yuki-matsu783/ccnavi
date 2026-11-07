"""`ccnavi ops …`（操作記録の読み書きの入口）と、hook の止めを確かめる。"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from tests import ROOT, common_path, oprecord
from tests.inproc import run_ccnavi

REPO = {"host": "github.com", "kind": "github", "path": "o/r"}


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class OpsCliTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-ops-cli-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, ".claude"))
        _write(common_path(self.root, "rules"), json.dumps({"version": 1}))
        self.state = os.path.join(self.root, "logs", "state")
        self.env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        self.env.pop("CLAUDE_PROJECT_DIR", None)

    def ccnavi(self, *words):
        return run_ccnavi(
            ["--root", self.root, "--state", self.state, *words], cwd=ROOT, env=self.env
        )

    def begin(self):
        return oprecord.begin(
            self.state,
            "decide",
            repo=REPO,
            parent="i0001",
            phase=1,
            stages=[("reply", "reply"), ("issue", "issue")],
        )

    def record(self, op_id):
        """操作記録の中身。消えていれば None。"""
        path = os.path.join(self.state, "ops", f"{op_id}.json")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def test_pending_lists_unfinished_records_one_per_line(self):
        op_id = self.begin()
        done = self.ccnavi("ops", "stage-set", op_id, "reply", "sent")
        self.assertEqual(done.returncode, 0, done.stderr)
        result = self.ccnavi("ops", "pending")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), [op_id, "decide", "issue"])

    def test_stage_set_and_done(self):
        op_id = self.begin()
        for stage in ("reply", "issue"):
            done = self.ccnavi("ops", "stage-set", op_id, stage, "sent", "https://x/1", "me")
            self.assertEqual(done.returncode, 0, done.stderr)
        shown = json.loads(self.ccnavi("ops", "show", op_id).stdout)
        self.assertEqual(shown["stages"][1]["url"], "https://x/1")
        self.assertEqual(self.ccnavi("ops", "done", op_id).stdout.strip(), "done")
        self.assertIsNone(self.record(op_id))

    def test_done_keeps_an_unfinished_record(self):
        op_id = self.begin()
        self.assertEqual(self.ccnavi("ops", "done", op_id).stdout.strip(), "open")
        self.assertIsNotNone(self.record(op_id))

    def test_bad_input_is_refused_without_writing(self):
        op_id = self.begin()
        for words in (
            ("ops", "stage-set", op_id, "reply", "weird"),
            ("ops", "stage-set", op_id, "nope", "sent"),
            ("ops", "stage-set", "../x", "reply", "sent"),
            ("ops", "show", "0" * 32),
            ("ops", "unknown"),
        ):
            with self.subTest(words=words):
                self.assertNotEqual(self.ccnavi(*words).returncode, 0)
        self.assertEqual(self.record(op_id)["stages"][0]["state"], "pending")


class OpsHookTest(unittest.TestCase):
    """エージェントが偽の送信済みを書けないこと。読む副命令は止めない。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-ops-hook-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, ".claude"))
        _write(common_path(self.root, "rules"), json.dumps({"version": 1}))

    def decision(self, command, agent=""):
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "cwd": self.root,
            "session_id": "s1",
            "tool_input": {"command": command},
        }
        if agent:
            payload["agent_id"] = agent
        env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        env.pop("CLAUDE_PROJECT_DIR", None)
        result = run_ccnavi(
            ["--root", self.root, "--mode", "enable"],
            input=json.dumps(payload),
            cwd=ROOT,
            env=env,
        )
        if not result.stdout.strip():
            return ""
        return json.loads(result.stdout)["hookSpecificOutput"].get("permissionDecision", "")

    def test_writing_subcommands_are_stopped_for_everyone(self):
        op = "a" * 32
        for command in (
            f"ccnavi ops stage-set {op} reply sent",
            f"ccnavi ops done {op}",
            f"python -m ccnavi ops stage-set {op} reply sent",
        ):
            for agent in ("", "a1"):
                with self.subTest(command=command, agent=agent):
                    self.assertEqual(self.decision(command, agent), "deny")

    def test_reading_subcommands_are_not_stopped_by_the_state_guard(self):
        self.assertNotEqual(self.decision("ccnavi ops pending"), "deny")
        self.assertNotEqual(self.decision("ccnavi ops show " + "a" * 32), "deny")


if __name__ == "__main__":
    unittest.main()
