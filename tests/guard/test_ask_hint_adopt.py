"""確認（ask）の理由が、あとのスクリプト側の拒否を隠さないこと
（direct-worktree-confirm の `--adopt`）。

承認前の親ワークツリーに `ccnavi-worktree.sh <親> --adopt` を打つと、先にルールの確認が当たる。
非対話では確認の理由がエージェントに返るが、「了承されたら打ち直す」とだけ読めると、打ち直しても
スクリプトが「チケットに結び付くワークツリーです」と断る。理由が、通らない条件を先に言う。

運用に使っている rules.yml をそのまま使い、非対話（CLAUDE_CODE_SESSION_ATTENDED=0）の
hook の応答を読む。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from ccnavi.hook import ccnavimd
from tests import ROOT
from tests.inproc import run_ccnavi

RULES = os.path.join(ROOT, ".ccnavi", "config", "common", "rules.yml")
WT = "sh /repo/.ccnavi/scripts/ccnavi-worktree.sh"


class AskHintAdoptTest(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="ccnavi-askhint-")
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        common = os.path.join(self.repo, ".ccnavi", "config", "common")
        os.makedirs(common)
        shutil.copy(RULES, os.path.join(common, "rules.yml"))

    def reason(self, command, attended="0"):
        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": "s1",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment["CCNAVI_LEARNING"] = "disable"
        environment[ccnavimd.ATTENDED_ENV] = attended
        done = run_ccnavi(
            ["--root", self.repo], input=json.dumps(payload), cwd=ROOT, env=environment
        )
        out = json.loads(done.stdout)["hookSpecificOutput"]
        return out["permissionDecision"], out.get("permissionDecisionReason", "")

    def test_非対話の_adopt_は確認で止まり_通らない条件を言う(self):
        decision, said = self.reason(f"{WT} parent-1 --adopt --base main")

        self.assertEqual(decision, "ask")
        self.assertIn("direct-worktree-confirm", said)
        self.assertIn("了承", said)
        self.assertIn("チケットに結び付く", said)
        self.assertIn("了承されても通りません", said)
        self.assertIn("マージリクエストで出します", said)

    def test_ほかの指定は従来どおり_adopt_の断りを足さない(self):
        decision, said = self.reason(f"{WT} feature-1 --base main")

        self.assertEqual(decision, "ask")
        self.assertIn("了承", said)
        self.assertNotIn("通りません", said)

    def test_対話では確認の理由に足さない(self):
        decision, said = self.reason(f"{WT} parent-1 --adopt --base main", attended="1")

        self.assertEqual(decision, "ask")
        self.assertNotIn("通りません", said)


if __name__ == "__main__":
    unittest.main()
