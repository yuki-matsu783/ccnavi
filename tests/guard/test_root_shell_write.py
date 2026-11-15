"""シェルからワークスペースルート直下へ書く形を止めることの受入テスト。

Write / Edit はルールファイルの `workspace-root` が止める。シェルの書き込み先を解いた絶対パスを
同じルールに適用するので、`sed -i`・`>`・`tee`・`rm` などでも同じ置き場（`wip/handoff/`・
`scratchpad/`・`.claude/worktrees/`）だけが書ける。拒否の案内は置き場へ誘導する。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tests import ROOT
from tests.inproc import run_ccnavi

RULE_ID = "builtin:guard-root-shell-write"
RULES = os.path.join(".ccnavi", "config", "common", "rules.yml")


class RootShellWriteTest(unittest.TestCase):
    def setUp(self):
        self.repo = os.path.realpath(tempfile.mkdtemp(prefix="ccnavi-rootwrite-"))
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        subprocess.run(["git", "init", "--quiet"], cwd=self.repo, check=True)
        os.makedirs(os.path.join(self.repo, ".ccnavi", "config", "common"))
        shutil.copy(os.path.join(ROOT, RULES), os.path.join(self.repo, RULES))
        for place in ("wip/handoff", "scratchpad", ".claude/worktrees/wt"):
            os.makedirs(os.path.join(self.repo, *place.split("/")))

    def decide(self, command, cwd=""):
        """(判定, 理由)。ルートの決まりで止めたときだけ判定が deny になる。"""
        body = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "session_id": "s1",
            "cwd": os.path.join(self.repo, cwd) if cwd else self.repo,
        }
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        result = run_ccnavi(
            ["--root", self.repo, "--mode", "enable", "--guard-core-files", "enable"],
            input=json.dumps(body),
            cwd=ROOT,
            env=environment,
        )
        self.last = result
        if not result.stdout.strip():
            return "", ""
        out = json.loads(result.stdout)["hookSpecificOutput"]
        return out.get("permissionDecision", ""), out.get("permissionDecisionReason", "")

    def stopped(self, command, cwd=""):
        decision, reason = self.decide(command, cwd)
        return decision == "deny" and RULE_ID in reason

    # 書き込み先の読み（リダイレクト・`sed -i`・`tee`・`cp` の行き先・出力の指定・
    # 読めない行き先・引用符の中の山括弧・サブシェルの起点・乱れた引用符で落ちないこと）と
    # 止める・通すの答えは、Go の見本（go/internal/hook/shellguard の TestMatchesGolden）が
    # 確かめる。ここでは hook を通した代表だけを見る。

    def test_hook_stops_a_root_write_and_opens_the_drafts_places(self):
        self.assertTrue(self.stopped("sed -i 's/a/b/' README.md"))
        self.assertFalse(self.stopped("echo x > wip/handoff/a.md"))
        # payload の cwd が相対の行き先を決める。
        self.assertFalse(self.stopped("sed -i 's/a/b/' README.md", cwd=".claude/worktrees/wt"))

    def test_the_refusal_points_to_the_drafts_places_and_the_command(self):
        decision, reason = self.decide("cd docs && echo x > README.md")
        self.assertEqual("deny", decision)
        self.assertIn(RULE_ID, reason)
        self.assertIn("DENY_SHELL_WORKSPACE_ROOT", reason)
        self.assertIn("wip/handoff/", reason)
        self.assertIn("scratchpad/", reason)
        self.assertIn("書き込み先: README.md", reason)
        self.assertIn("読んだ箇所: echo x > README.md", reason)

    def test_サブシェルの直後の語もコマンド名のルールに当たる(self):
        # `);` の前後の空白で読みが変わらないことは Go（TestSubshellSemicolonSpacing）が見る。
        self.assertIn("prefer-webfetch", self.decide("(true); curl https://example.com")[1])


if __name__ == "__main__":
    unittest.main()
