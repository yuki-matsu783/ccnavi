"""ルールの `except:`（本体にヒットしても、除くものにヒットすればヒットしない扱い）を、
hook として呼んだときの判定。

読み込み（書式と苦情）・Rule の照合・lint・レイヤーのマージ・提案が書き戻す形は、
`go/internal/policy`（rules・rulelint・ruleload）と `go/internal/entry/suggest` の go test が見る。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from tests import ROOT, common_path
from tests.inproc import run_ccnavi

# workspace-root を except で書いた形。rules.yml の 1 文字ずつの場合分けを置き換える。
WORKSPACE_ROOT = {
    "id": "workspace-root",
    "match": "Write|Edit|NotebookEdit",
    "regex": r"^{root}[\\/]",
    "except": [
        {"regex": r"^{root}[\\/]wip[\\/]handoff(?:[\\/]|$)"},
        {"regex": r"^{root}[\\/]scratchpad(?:[\\/]|$)"},
        {"regex": r"^{root}[\\/]\.claude[\\/]worktrees(?:[\\/]|$)"},
    ],
    "message": "main では編集しない",
}


class JudgeTest(unittest.TestCase):
    """hook の判定で except が効く。入れ子のコマンド（sudo の中）にも同じにヒットする。"""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.dir.name)
        self.addCleanup(self.dir.cleanup)
        path = common_path(self.root, "rules")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": 1,
                    "deny": [
                        WORKSPACE_ROOT,
                        {
                            "id": "publish",
                            "match": "Bash",
                            "regex": r"(^|\x00)npm publish\b",
                            "except": [{"regex": r"(^|\x00)npm publish --dry-run\b"}],
                            "message": "公開しない",
                        },
                    ],
                    "allow": [{"id": "all", "match": "Read", "glob": "*"}],
                },
                f,
            )

    def judge(self, tool: str, tool_input: dict) -> dict:
        payload = json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": tool,
                "cwd": self.root,
                "tool_input": tool_input,
            }
        )
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        done = run_ccnavi(
            [
                "--root",
                self.root,
                "--mode",
                "enable",
                "--guard-core-files",
                "disable",
            ],
            input=payload,
            cwd=ROOT,
            env=environment,
        )
        if not done.stdout.strip():
            return {}
        return json.loads(done.stdout)["hookSpecificOutput"]

    def decision(self, tool: str, tool_input: dict) -> str:
        return self.judge(tool, tool_input).get("permissionDecision", "")

    def test_パス(self):
        path = os.path.join(self.root, "README.md")
        self.assertEqual(self.decision("Write", {"file_path": path}), "deny")
        kept = os.path.join(self.root, "wip", "handoff", "a.md")
        self.assertNotEqual(self.decision("Write", {"file_path": kept}), "deny")

    def test_コマンドと入れ子のコマンド(self):
        def says(command: str) -> tuple[str, bool]:
            out = self.judge("Bash", {"command": command})
            reason = out.get("permissionDecisionReason", "")
            return out.get("permissionDecision", ""), (
                "(rule: .ccnavi/config/common/rules.yml:publish)" in reason
            )

        self.assertEqual(says("npm publish"), ("deny", True))
        self.assertEqual(says("npm publish --dry-run"), ("ask", False))
        # sudo が中で実行するコマンドにも、同じ except が効く
        self.assertEqual(says("sudo npm publish"), ("deny", True))
        self.assertEqual(says("sudo npm publish --dry-run"), ("ask", False))
        # Bash ではコマンド列の全体に適用する（lint が warn で言う形）
        self.assertEqual(says("npm publish; npm publish --dry-run"), ("ask", False))


if __name__ == "__main__":
    unittest.main()
