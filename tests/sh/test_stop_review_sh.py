"""`scripts/ccnavi-stop-review.sh`: Stop の AI 判定の agent hook の登録・解除・点検。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tests import ROOT

SCRIPT = os.path.join(ROOT, "scripts", "ccnavi-stop-review.sh")
HAS_JQ = shutil.which("jq") is not None


@unittest.skipUnless(HAS_JQ, "jq が要る")
class StopReviewSetupTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.settings = os.path.join(self.root, ".claude", "settings.json")

    def sh(self, *args):
        return subprocess.run(
            ["sh", SCRIPT, *args, "--root", self.root],
            capture_output=True,
            text=True,
            check=False,
        )

    def write(self, data):
        os.makedirs(os.path.dirname(self.settings), exist_ok=True)
        with open(self.settings, "w", encoding="utf-8") as handle:
            json.dump(data, handle)

    def read(self):
        with open(self.settings, encoding="utf-8") as handle:
            return json.load(handle)

    def agents(self, data=None):
        data = data or self.read()
        return [
            h
            for group in data.get("hooks", {}).get("Stop", [])
            for h in group.get("hooks", [])
            if h.get("type") == "agent"
        ]

    def test_default_is_not_registered(self):
        done = self.sh("check")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("登録されていません", done.stdout)
        self.assertFalse(os.path.exists(self.settings))

    def test_register_keeps_other_definitions(self):
        self.write(
            {
                "env": {"KEEP": "1", "CCNAVI_STOP_REVIEW_AGENT": "0"},
                "permissions": {"allow": ["Bash(ls)"]},
                "hooks": {
                    "Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "x"}]}]
                },
            }
        )
        done = self.sh("register", "--judge-type", "judge-1", "--model", "haiku")
        self.assertEqual(done.returncode, 0, done.stderr)
        data = self.read()
        self.assertEqual(data["permissions"], {"allow": ["Bash(ls)"]})
        self.assertEqual(data["env"]["KEEP"], "1")
        self.assertEqual(data["env"]["CCNAVI_STOP_REVIEW_AGENT"], "0")  # 既にある値は触らない
        self.assertEqual(data["env"]["CCNAVI_STOP_REVIEW_JUDGE_TYPE"], "judge-1")
        commands = [h for g in data["hooks"]["Stop"] for h in g["hooks"] if h["type"] == "command"]
        self.assertEqual(commands, [{"type": "command", "command": "x"}])
        (agent,) = self.agents(data)
        self.assertTrue(agent["prompt"].startswith("[ccnavi-stop-review]"))
        self.assertIn("$ARGUMENTS", agent["prompt"])
        self.assertIn("stop-review material", agent["prompt"])
        self.assertIn("stop-review record", agent["prompt"])
        self.assertEqual(agent["model"], "haiku")
        self.assertTrue(os.path.exists(self.settings + ".bak"))

    def test_register_sets_the_marker_env_and_is_idempotent(self):
        self.assertEqual(self.sh("register").returncode, 0)
        before = self.read()
        self.assertEqual(before["env"]["CCNAVI_STOP_REVIEW_AGENT"], "1")
        done = self.sh("register")
        self.assertIn("すでに登録", done.stdout)
        self.assertEqual(self.read(), before)
        self.assertEqual(self.sh("check").returncode, 0)

    def test_unregister_removes_only_ours(self):
        self.write({"hooks": {"Stop": [{"hooks": [{"type": "agent", "prompt": "他人の判定"}]}]}})
        self.sh("register")
        self.assertEqual(len(self.agents()), 2)
        self.assertEqual(self.sh("unregister").returncode, 0)
        data = self.read()
        self.assertEqual([a["prompt"] for a in self.agents(data)], ["他人の判定"])
        self.assertNotIn("CCNAVI_STOP_REVIEW_AGENT", data.get("env", {}))
        self.assertIn("外すものはありません", self.sh("unregister").stdout)

    def test_unregister_leaves_no_empty_stop_entry(self):
        self.sh("register")
        self.sh("unregister")
        self.assertNotIn("Stop", self.read().get("hooks", {}))

    def test_check_reports_mismatches(self):
        self.sh("register")
        data = self.read()
        del data["env"]["CCNAVI_STOP_REVIEW_AGENT"]
        self.write(data)
        done = self.sh("check")
        self.assertEqual(done.returncode, 1)
        self.assertIn("CCNAVI_STOP_REVIEW_AGENT=1 がありません", done.stdout)
        data["env"]["CCNAVI_STOP_REVIEW_AGENT"] = "1"
        data["hooks"]["Stop"].append(data["hooks"]["Stop"][0])
        self.write(data)
        done = self.sh("check")
        self.assertEqual(done.returncode, 1)
        self.assertIn("重複", done.stdout)
        # register は重複を 1 件に直す
        self.sh("register")
        self.assertEqual(len(self.agents()), 1)
        self.assertEqual(self.sh("check").returncode, 0)

    def test_broken_settings_are_never_rewritten(self):
        os.makedirs(os.path.dirname(self.settings))
        with open(self.settings, "w", encoding="utf-8") as handle:
            handle.write("[1, 2]")
        done = self.sh("register")
        self.assertEqual(done.returncode, 2)
        with open(self.settings, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "[1, 2]")

    def test_bad_arguments(self):
        self.assertEqual(subprocess.run(["sh", SCRIPT], capture_output=True).returncode, 2)
        self.assertEqual(subprocess.run(["sh", SCRIPT, "nope"], capture_output=True).returncode, 2)
        self.assertEqual(self.sh("check", "--bogus").returncode, 2)


if __name__ == "__main__":
    unittest.main()
