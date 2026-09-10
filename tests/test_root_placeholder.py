"""ルールの `{root}` がワークスペースルートに置き換わることの受入テスト。
道具を外から叩いて応答だけを見る。

「ワークスペースルートの下で、かつ .claude/worktrees/ の外」を止めるルールが、main の側では止め、
作業ツリーの中では止めず、ワークスペースの外には何も言わないこと。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

from ccnavi import rules

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# このリポジトリの rules.yml と同じ形。先読みを使わずに 1 段目で場合分けする。
MAIN_TREE = (
    r"^{root}[\\/](?:[^.\\/][^\\/]*|\.[^c\\/][^\\/]*|\.c[^l\\/][^\\/]*|\.claude[\\/][^w\\/][^\\/]*)"
)


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class RootPlaceholderTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.dir.name)
        self.addCleanup(self.dir.cleanup)
        self.rules = write(
            os.path.join(self.root, "rules.yml"),
            json.dumps(
                {
                    "version": 3,
                    "deny": [
                        {
                            "id": "main-tree",
                            "match": "Write|Edit",
                            "regex": MAIN_TREE,
                            "message": "main では編集しない",
                        },
                        {
                            "id": "wip",
                            "match": "Write",
                            "glob": "{root}/wip/*",
                            "message": "glob でもワークスペースルートを指せる",
                        },
                    ],
                    "allow": [
                        {"id": "worktrees", "match": "Write|Edit", "glob": "*/.claude/worktrees/*"}
                    ],
                }
            ),
        )

    def judge(self, tool: str, path: str) -> dict:
        payload = json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": tool,
                "cwd": self.root,
                "tool_input": {"file_path": path},
            }
        )
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        done = subprocess.run(
            [
                sys.executable,
                "-m",
                "ccnavi",
                "--root",
                self.root,
                "--mode",
                "enable",
                "--rules",
                self.rules,
                "--log",
                "",
                "--state",
                "",
                "--approved",
                "",
                "--guard-core-files",
                "disable",
            ],
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=ROOT,
            env=environment,
        )
        if not done.stdout.strip():
            return {}
        return json.loads(done.stdout)["hookSpecificOutput"]

    def test_main_is_denied_and_worktrees_are_allowed(self):
        denied = [
            os.path.join(self.root, "README.md"),
            os.path.join(self.root, "ccnavi", "cli.py"),
            os.path.join(self.root, ".gitignore"),
            os.path.join(self.root, ".claude", "ccnavi", "rules.yml"),
            os.path.join(self.root, ".claude", "settings.json"),
            os.path.join(self.root, ".claude", "skills", "x", "SKILL.md"),
        ]
        for path in denied:
            with self.subTest(path=path):
                out = self.judge("Write", path)
                self.assertEqual(out.get("permissionDecision"), "deny", out)
                self.assertIn("main-tree", out.get("permissionDecisionReason", ""))
        allowed = [
            os.path.join(self.root, ".claude", "worktrees", "x", "README.md"),
            os.path.join(self.root, ".claude", "worktrees", "x", "ccnavi", "cli.py"),
            os.path.join(self.root, ".claude", "worktrees", "x", ".claude", "ccnavi", "rules.yml"),
        ]
        for path in allowed:
            with self.subTest(path=path):
                out = self.judge("Edit", path)
                self.assertNotEqual(out.get("permissionDecision"), "deny", out)
                self.assertNotIn("main-tree", out.get("permissionDecisionReason", ""))

    def test_outside_the_root_is_not_mentioned(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            out = self.judge("Write", os.path.join(os.path.realpath(elsewhere), "README.md"))
        self.assertNotEqual(out.get("permissionDecision"), "deny", out)
        self.assertIn("UNDECLARED", out.get("permissionDecisionReason", ""))

    def test_glob_form_and_spelling_of_the_root(self):
        out = self.judge("Write", os.path.join(self.root, "wip", "a.md"))
        self.assertIn("wip", out.get("permissionDecisionReason", ""))
        # 綴りを変えても行き着く先で当たる。`..` と、区別しない機械では大文字小文字。
        detour = os.path.join(self.root, "docs", "..", "README.md")
        self.assertEqual(self.judge("Write", detour).get("permissionDecision"), "deny")
        if os.path.normcase("A") == "a":
            swapped = os.path.join(self.root.swapcase(), "README.md")
            self.assertEqual(self.judge("Write", swapped).get("permissionDecision"), "deny")

    def test_placeholder_needs_a_root(self):
        _, problems = rules.load(self.rules)
        self.assertTrue(any("{root}" in str(p) for p in problems), problems)
        rule_set, problems = rules.load(self.rules, self.root)
        self.assertEqual(problems, [])
        # 書いた綴りは残り、置き換わるのは翻訳後の式だけ。
        self.assertEqual(rule_set.deny[0].regex, MAIN_TREE)
        self.assertNotIn("{root}", rule_set.deny[0].compiled.pattern)


if __name__ == "__main__":
    unittest.main()
