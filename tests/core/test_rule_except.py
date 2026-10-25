"""ルールの `except:`（本体にヒットしても、除くものにヒットすればヒットしない扱い）のテスト。

読み込み（書式と苦情）、判定（Rule.matches と hook の判定）、lint、レイヤーのマージ、
提案が書き戻す形を見る。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

import yaml

from ccnavi.entry import suggest
from ccnavi.policy import rule_lint, ruleload, rules
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


def build(raw: dict, section: str = rules.DENY, root: str = "/r") -> tuple:
    """ルール 1 件を読み込む。返すのは (ルール or None, 苦情のリスト)。"""
    rule_set, problems = rules.parse({"version": 1, section: [raw]}, root)
    found = rule_set.section(section)
    return (found[0] if found else None), problems


def errors(problems: list) -> list[str]:
    return [p.detail for p in problems if p.severity == rules.SEVERITY_ERROR]


class ParseTest(unittest.TestCase):
    def test_glob_と_regex_を読む(self):
        rule, problems = build(
            {
                "id": "x",
                "match": "Write",
                "glob": "*/secret/*",
                "except": [{"glob": "*/secret/public/*"}, {"regex": r"\.md$"}],
                "message": "m",
            }
        )
        self.assertEqual(problems, [])
        self.assertEqual(
            [e.written() for e in rule.excepts],
            [{"glob": "*/secret/public/*"}, {"regex": r"\.md$"}],
        )
        self.assertTrue(rule.matches("Write", "/a/secret/key.pem"))
        self.assertFalse(rule.matches("Write", "/a/secret/public/key.pem"))
        self.assertFalse(rule.matches("Write", "/a/secret/README.MD"))  # 大文字小文字を区別しない
        # glob の区切りは `\\` にもヒットする（本体と同じ翻訳）
        self.assertFalse(rule.matches("Write", r"C:\a\secret\public\key.pem"))

    def test_except_が無ければ今までどおり(self):
        rule, problems = build({"id": "x", "match": "Write", "glob": "*", "message": "m"})
        self.assertEqual(problems, [])
        self.assertEqual(rule.excepts, [])
        self.assertTrue(rule.matches("Write", "/a"))

    def test_match_の見方は変わらない(self):
        rule, _ = build(
            {
                "id": "x",
                "match": "Write",
                "glob": "*",
                "except": [{"glob": "*.md"}],
                "message": "m",
            }
        )
        self.assertFalse(rule.matches("Edit", "/a/b.py"))
        self.assertTrue(rule.matches("Write", "/a/b.py"))

    def test_root_を置き換える(self):
        with tempfile.TemporaryDirectory() as d:
            root = os.path.realpath(d)
            rule, problems = build(
                {
                    "id": "x",
                    "match": "Write",
                    "regex": "^{root}[\\\\/]",
                    "except": [{"glob": "{root}/wip/*"}],
                    "message": "m",
                },
                root=root,
            )
            self.assertEqual(problems, [])
            self.assertTrue(rule.matches("Write", os.path.join(root, "a.md")))
            self.assertFalse(rule.matches("Write", os.path.join(root, "wip", "a.md")))
            self.assertEqual(rule.excepts[0].glob, "{root}/wip/*")  # 書いた表記は残す

    def test_not_root_を展開する(self):
        with tempfile.TemporaryDirectory() as d:
            root = os.path.realpath(d)
            rule, problems = build(
                {
                    "id": "x",
                    "match": "Read",
                    "glob": "*.pem",
                    "except": [{"regex": "^{!root}.*"}],
                    "message": "m",
                },
                root=root,
            )
            self.assertEqual(problems, [])
            self.assertTrue(rule.matches("Read", os.path.join(root, "k.pem")))
            self.assertFalse(rule.matches("Read", "/elsewhere/k.pem"))


class ErrorTest(unittest.TestCase):
    def check(self, excepts: object, *words: str, root: str = "/r") -> None:
        rule, problems = build(
            {"id": "x", "match": "Write", "glob": "*", "except": excepts, "message": "m"},
            root=root,
        )
        self.assertIsNone(rule, problems)
        found = errors(problems)
        self.assertEqual(len(found), 1, problems)
        for word in words:
            self.assertIn(word, found[0])
        self.assertTrue(all(p.rule == "deny:x" for p in problems))

    def test_書式の誤り(self):
        self.check([], "空")
        self.check(None, "リストではない")
        self.check({"glob": "*"}, "リストではない")
        self.check("*.md", "リストではない")
        self.check(["*.md"], "except[0]", "マッピングではない")
        self.check([{"glob": "*.md", "regex": "x"}], "except[0]", "両方")
        self.check([{"glob": "*.md"}, {}], "except[1]", "glob も regex もありません。")
        self.check([{"glob": ""}], "except[0]", "glob も regex もありません。")
        self.check([{"glob": "*.md", "why": "x"}], "except[0]", "未対応のキー", "why")
        self.check([{"match": "Write"}], "except[0]", "未対応のキー")

    def test_文字列でない値は誤り(self):
        # 引用符の無い `on` は真偽値に読まれる。黙って 'True' として適用しない
        # （when の個別条件と同じ）
        for item in ({"glob": True}, {"regex": 5}, {"glob": ["*.md"]}, {"glob": None, "regex": 0}):
            self.check([item], "except[0]", "文字列ではない")
        self.check(yaml.safe_load("- glob: on"), "except[0]", "文字列ではない")

    def test_本体と同じ制約(self):
        self.check([{"regex": "a(?=b)"}], "except[0]", "先読み")
        self.check([{"regex": "a(?<!b)"}], "except[0]", "否定後読み")
        self.check([{"regex": r"(a)\1"}], "except[0]", "後方参照")
        self.check([{"regex": "a("}], "except[0]", "正規表現として組み立てられない")
        self.check([{"glob": "{root}/x"}], "except[0]", "{root}", root="")
        self.check([{"glob": "{!root}/x"}], "except[0]", "glob")
        self.check([{"regex": "x{!root}"}], "except[0]", "^")

    def test_not_root_を組み立てられないとき(self):
        long_root = "/" + "a" * rules.MAX_ROOT_LEN
        raw = {
            "id": "x",
            "match": "Write",
            "glob": "*.pem",
            "except": [{"regex": "^{!root}.*"}],
        }
        # deny は except を外して適用する（止める側に外れる）
        rule, problems = build({**raw, "message": "m"}, root=long_root)
        self.assertIsNotNone(rule)
        self.assertEqual(rule.excepts, [])
        self.assertTrue(rule.matches("Write", "/elsewhere/k.pem"))
        self.assertTrue(any("except[0]" in d for d in errors(problems)))
        # allow は広がると通す範囲が増えるので、ルールごと使わない
        rule, problems = build(raw, section=rules.ALLOW, root=long_root)
        self.assertIsNone(rule)
        self.assertTrue(errors(problems))

    def test_本体を組み立てられないときは_except_を付けない(self):
        long_root = "/" + "a" * rules.MAX_ROOT_LEN
        rule, problems = build(
            {
                "id": "x",
                "match": "Write",
                "regex": "^{!root}.*",
                "except": [{"glob": "*.md"}],
                "message": "m",
            },
            root=long_root,
        )
        self.assertEqual(rule.excepts, [])
        self.assertTrue(rule.matches("Write", "/elsewhere/a.md"))
        self.assertTrue(errors(problems))


class WorkspaceRootTest(unittest.TestCase):
    """rules.yml の workspace-root を except で書いた形の境界。"""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = os.path.realpath(self.dir.name)
        rule, problems = build(WORKSPACE_ROOT, root=self.root)
        self.assertEqual(problems, [])
        self.rule = rule

    def hits(self, tail: str) -> bool:
        return self.rule.matches("Write", self.root + tail)

    def test_置き場だけが通る(self):
        for tail in [
            "/wip/handoff/a.md",
            "/wip/handoff/x/y.sh",
            "/wip/handoff",
            "/wip/handoff/",
            "/WIP/Handoff/a.md",
            "/scratchpad/a.md",
            "/scratchpad",
            r"\scratchpad\a.md",
            r"\wip\handoff\a.md",
            "/wip\\handoff/a.md",
            "/.claude/worktrees/x/a.md",
            "/.claude/worktrees",
            r"\.claude\worktrees\x\a.md",
        ]:
            with self.subTest(tail=tail):
                self.assertFalse(self.hits(tail))

    def test_それ以外は止まる(self):
        for tail in [
            "/README.md",
            "/w",
            "/wip",
            "/wip/",
            "/wip/a.md",
            "/wipx/a.md",
            "/wip/handof",
            "/wip/scratchpad/a.md",
            "/scratchpadx/a.md",
            "/wip/handoffx",
            "/wip/handoffx/a.md",
            "/.cfoo",
            "/.claude/settings.json",
            "/.claude/worktreesx/a.md",
            # 1 本の regex では通っていた形。except で書くと、置き場の外として止まる
            "/.clang-format",
            "/.claude",
            "/.claude/wx/a.md",
            r"\README.md",
        ]:
            with self.subTest(tail=tail):
                self.assertTrue(self.hits(tail))

    def test_ルートの外とルートそのものにはヒットしない(self):
        self.assertFalse(self.rule.matches("Write", "/elsewhere/a.md"))
        self.assertFalse(self.rule.matches("Write", self.root))


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


class LintTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.dir.name)
        self.addCleanup(self.dir.cleanup)

    def lint(self, deny: list, allow: list | None = None) -> list:
        path = os.path.join(self.root, "rules.yml")
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                {
                    "version": 1,
                    "deny": deny,
                    "allow": allow or [{"id": "r", "match": "Read", "glob": "*"}],
                },
                f,
                allow_unicode=True,
            )
        return rule_lint.file_problems(path, self.root)

    def details(self, problems: list, severity: str) -> list[str]:
        return [f"{p.rule} {p.detail}" for p in problems if p.severity == severity]

    def test_正しい形は何も言わない(self):
        problems = self.lint([WORKSPACE_ROOT])
        self.assertEqual(self.details(problems, rules.SEVERITY_ERROR), [])
        self.assertEqual(self.details(problems, rules.SEVERITY_WARN), [])

    def test_書式の誤りは_error(self):
        bad = {**WORKSPACE_ROOT, "except": [{"glob": "*", "regex": "x"}]}
        found = self.details(self.lint([bad]), rules.SEVERITY_ERROR)
        self.assertTrue(any("workspace-root" in d and "except[0]" in d for d in found), found)

    def test_何にでもヒットする_except(self):
        rule = {"id": "x", "match": "Write", "glob": "*.pem", "except": [{"glob": "*"}]}
        found = self.details(self.lint([{**rule, "message": "m"}]), rules.SEVERITY_WARN)
        self.assertTrue(any("except[0] は何にでもヒットする" in d for d in found), found)

    def test_コマンドの_except(self):
        rule = {
            "id": "push",
            "match": "Bash",
            "glob": "*git push*",
            "except": [{"glob": "*--dry-run*"}],
            "message": "m",
        }
        found = self.details(self.lint([rule]), rules.SEVERITY_WARN)
        self.assertTrue(any("コマンド列の全体" in d for d in found), found)
        # allow は外れても止める側に寄るだけなので言わない
        allow = {k: v for k, v in rule.items() if k != "message"}
        found = self.details(self.lint([WORKSPACE_ROOT], [allow]), rules.SEVERITY_WARN)
        self.assertFalse(any("コマンド列の全体" in d for d in found), found)

    def test_コマンド名からつないだ_except_は言わない(self):
        # コマンド名のあとを `[^\x00]*` でつなぐ式は、同じコマンドの中の語にだけヒットする
        rule = {
            "id": "push",
            "match": "Bash",
            "regex": r"git\s+push\b",
            "except": [{"regex": r"git\s+push\b[^\x00]*\s--dry-run(\s|$)"}],
            "message": "m",
        }
        found = self.details(self.lint([rule]), rules.SEVERITY_WARN)
        self.assertFalse(any("コマンド列の全体" in d for d in found), found)

    def test_コマンドの切れ目を越える_except_は言う(self):
        base = {"id": "push", "match": "Bash", "regex": r"git\s+push\b", "message": "m"}
        for pattern in (
            r"\s--dry-run(\s|$)",  # コマンド名が無い
            r"git\s+push\b.*--dry-run",  # `.` は切れ目も読む
            r"git\s+push\b\S*\s--dry-run",  # `\S` は切れ目も読む
            r"git\s+push\b[^\n]*--dry-run",  # 切れ目を除かない否定のクラス
            r"[^\x00]*\s--dry-run",  # 頭にコマンド名が無い
        ):
            with self.subTest(pattern=pattern):
                rule = {**base, "except": [{"regex": pattern}]}
                found = self.details(self.lint([rule]), rules.SEVERITY_WARN)
                self.assertTrue(any("コマンド列の全体" in d for d in found), found)
        # 1 件でも切れ目を越える式があれば言う
        rule = {
            **base,
            "except": [
                {"regex": r"git\s+push\b[^\x00]*\s--dry-run(\s|$)"},
                {"regex": r"--force"},
            ],
        }
        found = self.details(self.lint([rule]), rules.SEVERITY_WARN)
        self.assertTrue(any("コマンド列の全体" in d for d in found), found)

    def test_stop_を除くと何も起きない(self):
        stop = {
            "id": "nudge",
            "match": "Stop",
            "glob": "*",
            "except": [{"glob": "(stop)"}],
            "every": 5,
            "additionalContext": "x",
        }
        found = self.details(self.lint([WORKSPACE_ROOT], [stop]), rules.SEVERITY_WARN)
        self.assertTrue(any("(stop)" in d for d in found), found)


class MergeTest(unittest.TestCase):
    def rule_set(self, raw: dict) -> rules.RuleSet:
        rule_set, problems = rules.parse({"version": 1, "deny": [raw]}, "/r")
        self.assertEqual(errors(problems), [])
        return rule_set

    def test_except_はマージで落ちない(self):
        base = self.rule_set({"id": "a", "match": "Write", "glob": "*", "message": "m"})
        extra = self.rule_set({**WORKSPACE_ROOT, "id": "b"})
        ruleload.prefix_ids(extra, "lib")
        ruleload.merge_rules(base, extra, "lib")
        merged = base.deny[1]
        self.assertEqual(merged.id, "lib:b")
        self.assertEqual(len(merged.excepts), 3)
        self.assertFalse(merged.matches("Write", "/r/wip/handoff/a.md"))

    def test_except_だけが違えば別の定義(self):
        with_except = {"id": "a", "match": "Write", "glob": "*", "message": "m"}
        base = self.rule_set({**with_except, "except": [{"glob": "*.md"}]})
        extra = self.rule_set(with_except)
        problems = ruleload.merge_rules(base, extra, "lib")
        self.assertEqual(len(base.deny), 2)
        self.assertEqual([p.severity for p in problems], [rules.SEVERITY_WARN])
        # 全欄が同じなら後ろを破棄する
        same = self.rule_set({**with_except, "except": [{"glob": "*.md"}]})
        problems = ruleload.merge_rules(base, same, "lib")
        self.assertEqual(len(base.deny), 2)
        self.assertEqual([p.severity for p in problems], [rules.SEVERITY_INFO])


class WrittenTest(unittest.TestCase):
    def test_提案が書き戻す形に_except_が残る(self):
        rule, _ = build(WORKSPACE_ROOT)
        written = suggest._written_rule(rule)
        self.assertEqual(written["except"], WORKSPACE_ROOT["except"])
        again, problems = build(written)
        self.assertEqual(errors(problems), [])
        self.assertEqual(again.key(), rule.key())


if __name__ == "__main__":
    unittest.main()
