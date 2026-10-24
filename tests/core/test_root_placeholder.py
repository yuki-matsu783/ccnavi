"""ルールの `{root}` がワークスペースルートに置き換わることの受入テスト。
道具を外から呼んで応答だけを見る。

「ワークスペースルートの下で、かつ .claude/worktrees/ の外」を止めるルールが、main の側では止め、
ワークツリーの中では止めず、ワークスペースの外には何も言わないこと。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from ccnavi.policy import rules
from tests import ROOT, common_path
from tests.inproc import run_ccnavi

# このリポジトリの rules.yml と同じ形。先読みを使わずに 1 段目で場合分けする。
WORKSPACE_ROOT_RULE = (
    r"^{root}[\\/](?:[^.\\/][^\\/]*|\.[^c\\/][^\\/]*|\.c[^l\\/][^\\/]*|\.claude[\\/][^w\\/][^\\/]*)"
)


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class WorkspaceRootRealRuleTest(unittest.TestCase):
    """このリポジトリの rules.yml の workspace-root が、3 つの置き場だけを通すこと。

    通すのは wip/handoff/・scratchpad/・.claude/worktrees/。

    判定と同じ読み方（rules.parse）で組み立てて適用する。ルールは 1 本の regex でも、
    `except:` で置き場を除く形でも書けるので、式を取り出して適用すると書き方に縛られる。
    大文字小文字は判定と同じく区別しない。
    """

    def setUp(self):
        import yaml

        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = os.path.realpath(self.dir.name)
        with open(
            os.path.join(ROOT, ".ccnavi", "config", "common", "rules.yml"), encoding="utf-8"
        ) as f:
            raw = next(r for r in yaml.safe_load(f)["deny"] if r["id"] == "workspace-root")
        rule_set, problems = rules.parse({"version": 1, "deny": [raw]}, self.root)
        self.assertEqual([p for p in problems if p.severity == rules.SEVERITY_ERROR], [])
        self.rule = rule_set.deny[0]

    def hits(self, tail: str) -> bool:
        return self.rule.matches("Write", self.root + tail)

    def test_置き場だけが通る(self):
        for tail in [
            "/wip/handoff/a.md",
            "/wip/handoff/x/y.sh",
            r"\wip\handoff\a",
            "/wip/Handoff/a.md",
            "/scratchpad/a.md",
            "/.claude/worktrees/x/a.md",
            r"\.claude\worktrees\x\a.md",
        ]:
            with self.subTest(tail=tail):
                self.assertFalse(self.hits(tail))

    def test_それ以外は止まる(self):
        for tail in [
            "/scratchpadx/a.md",
            "/wip/scratchpad/a.md",
            "/wip/proposals/todo/a.md",
            "/wip/a.md",
            "/wip",
            "/w",
            "/wipx/a.md",
            "/wip/handoffx/a.md",
            "/README.md",
            "/.claude/settings.json",
            "/.cfoo",
            "/.ccnavi/config/common/rules.yml",
        ]:
            with self.subTest(tail=tail):
                self.assertTrue(self.hits(tail))


class RootPlaceholderTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.dir.name)
        self.addCleanup(self.dir.cleanup)
        # 共通レイヤーは既定の置き場へ。`--rules` は診断でだけ有効。
        self.rules = write(
            common_path(self.root, "rules"),
            json.dumps(
                {
                    "version": 1,
                    "deny": [
                        {
                            "id": "workspace-root",
                            "match": "Write|Edit",
                            "regex": WORKSPACE_ROOT_RULE,
                            "message": "main では編集しない",
                        },
                        {
                            "id": "wip",
                            "match": "Write",
                            "glob": "{root}/wip/*",
                            "message": "glob でもワークスペースルートを指せる。"
                            "'sh {root}/.ccnavi/scripts/ccnavi-git.sh rm -r wip' で消す",
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

    def test_main_is_denied_and_worktrees_are_allowed(self):
        denied = [
            os.path.join(self.root, "README.md"),
            os.path.join(self.root, "src", "ccnavi", "entry", "cli.py"),
            os.path.join(self.root, ".gitignore"),
            os.path.join(self.root, ".ccnavi", "config", "common", "rules.yml"),
            os.path.join(self.root, ".claude", "settings.json"),
            os.path.join(self.root, ".claude", "skills", "x", "SKILL.md"),
        ]
        for path in denied:
            with self.subTest(path=path):
                out = self.judge("Write", path)
                self.assertEqual(out.get("permissionDecision"), "deny", out)
                self.assertIn("workspace-root", out.get("permissionDecisionReason", ""))
        allowed = [
            os.path.join(self.root, ".claude", "worktrees", "x", "README.md"),
            os.path.join(
                self.root, ".claude", "worktrees", "x", "src", "ccnavi", "entry", "cli.py"
            ),
            os.path.join(
                self.root, ".claude", "worktrees", "x", ".ccnavi", "config", "common", "rules.yml"
            ),
        ]
        for path in allowed:
            with self.subTest(path=path):
                out = self.judge("Edit", path)
                self.assertNotEqual(out.get("permissionDecision"), "deny", out)
                self.assertNotIn("workspace-root", out.get("permissionDecisionReason", ""))

    def test_outside_the_root_is_not_mentioned(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            out = self.judge("Write", os.path.join(os.path.realpath(elsewhere), "README.md"))
        self.assertNotEqual(out.get("permissionDecision"), "deny", out)
        self.assertIn("UNDECLARED", out.get("permissionDecisionReason", ""))

    def test_glob_form_and_spelling_of_the_root(self):
        out = self.judge("Write", os.path.join(self.root, "wip", "a.md"))
        self.assertIn("wip", out.get("permissionDecisionReason", ""))
        # 表記を変えても行き着く先でヒットする。`..` と、大文字小文字。
        detour = os.path.join(self.root, "docs", "..", "README.md")
        self.assertEqual(self.judge("Write", detour).get("permissionDecision"), "deny")
        # ワークスペースルートのパスも、どの機械でも区別せずに適用する。区別する機械では
        # 別の場所を指すパスだが、それでも止める側を採る（`{root}` を機械で変えない）。
        swapped = os.path.join(self.root.swapcase(), "README.md")
        self.assertEqual(self.judge("Write", swapped).get("permissionDecision"), "deny")

    def test_message_names_the_root(self):
        """止めたときにモデルへ渡す文面の `{root}` も、ワークスペースルートの実パスになる。

        置き換わらないと、案内どおりに `sh {root}/...` を打っても `{root}` という
        ディレクトリは無く、どこからも届かない。
        """
        out = self.judge("Write", os.path.join(self.root, "wip", "a.md"))
        reason = out.get("permissionDecisionReason", "")
        spelled = self.root.replace("\\", "/")
        self.assertIn(f"'sh {spelled}/.ccnavi/scripts/ccnavi-git.sh rm -r wip'", reason)
        self.assertNotIn("{root}", reason)

    def test_written_message_is_kept(self):
        """書いた文面は残る。置き換えるのはモデルへ渡すときだけ（glob / regex と同じ）。"""
        rule_set, _ = rules.load(self.rules, self.root)
        wip = rule_set.deny[1]
        self.assertIn("{root}", wip.message)
        self.assertNotIn("{root}", wip.spoken_message())
        # ルートが渡らない読み方では置き換えない。
        self.assertEqual(rules.fill_root(wip.message, ""), wip.message)

    def test_placeholder_needs_a_root(self):
        _, problems = rules.load(self.rules)
        self.assertTrue(any("{root}" in str(p) for p in problems), problems)
        rule_set, problems = rules.load(self.rules, self.root)
        self.assertEqual(problems, [])
        # 書いた表記は残り、置き換わるのは翻訳後の式だけ。
        self.assertEqual(rule_set.deny[0].regex, WORKSPACE_ROOT_RULE)
        self.assertNotIn("{root}", rule_set.deny[0].compiled.pattern)


if __name__ == "__main__":
    unittest.main()
