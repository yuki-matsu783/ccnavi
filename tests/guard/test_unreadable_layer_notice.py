"""SessionStart で、読めないレイヤーのルールを 1 度だけ知らせること。

読めないレイヤーは空として扱われ、そのレイヤーの deny が黙って効かなくなる。
どのレイヤーを読めないと数えるかは Go のテスト（go/internal/policy/ruleload）が確かめる。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from tests import ROOT
from tests.guard.test_learning_notice import ENV
from tests.inproc import run_ccnavi


class UnreadableLayerTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-unreadable-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, ".claude"))
        # ワークスペースのプロジェクトのレイヤー（self）のルール。
        self.path = os.path.join(self.root, ".ccnavi", "config", "project", "rules.yml")

    def write(self, text):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_session_start_says_it_to_the_model_and_the_user(self):
        self.write("version: 1\ndeny: [: broken\n")
        payload = json.dumps(
            {"hook_event_name": "SessionStart", "source": "startup", "cwd": self.root}
        )
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        env.update(ENV)
        args = ["--root", self.root, "--mode", "enable", "--guard-core-files", "disable"]
        done = run_ccnavi(args, input=payload, cwd=ROOT, env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        body = json.loads(done.stdout)
        self.assertIn("読めません", body["systemMessage"])
        self.assertIn("読めません", body["hookSpecificOutput"]["additionalContext"])


if __name__ == "__main__":
    unittest.main()
