"""ルールの `when:`（組み合わせ条件）を、hook の判定・`--explain`・`--test-expectations` で見る。

読み込み（書式・苦情・上限）・組み合わせの評価・lint・レイヤーのマージ・提案が書き戻す形は、
`go/internal/policy`（rules・rulelint・ruleload）と `go/internal/entry/suggest` の go test が見る。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from tests import ROOT, common_path
from tests.inproc import run_ccnavi

# 「*.crt か、vault/ の下で .md でないもの」。組み込みの認証情報の土台（builtin:credentials）に
# ヒットしない名前にしてある（土台は設定で切れず、ヒットすると when の検査が土台の deny に紛れる）。
VAULT = {
    "id": "vault",
    "match": "Write|Edit",
    "when": {
        "any": [
            {"glob": "*.crt"},
            {"all": [{"glob": "*/vault/*"}, {"not": {"glob": "*.md"}}]},
        ]
    },
    "message": "キーは書かない",
}


class JudgeTest(unittest.TestCase):
    """hook の判定で when が効く。Bash はコマンド列の全体に適用し、
    入れ子のコマンドにもヒットする。"""

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
                        VAULT,
                        {
                            "id": "publish",
                            "match": "Bash",
                            "when": {
                                "all": [
                                    {"regex": r"(^|\x00)npm publish\b"},
                                    {"not": {"regex": r"--dry-run\b"}},
                                ]
                            },
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

    def says(self, command: str) -> tuple[str, bool]:
        out = self.judge("Bash", {"command": command})
        reason = out.get("permissionDecisionReason", "")
        return out.get("permissionDecision", ""), (
            "(rule: .ccnavi/config/common/rules.yml:publish)" in reason
        )

    def test_パス(self):
        decision = self.judge("Write", {"file_path": os.path.join(self.root, "k.crt")})
        self.assertEqual(decision.get("permissionDecision"), "deny")
        md = os.path.join(self.root, "vault", "a.md")
        self.assertNotEqual(
            self.judge("Write", {"file_path": md}).get("permissionDecision"), "deny"
        )

    def test_コマンドと入れ子のコマンド(self):
        self.assertEqual(self.says("npm publish"), ("deny", True))
        self.assertEqual(self.says("npm publish --dry-run"), ("ask", False))
        self.assertEqual(self.says("sudo npm publish"), ("deny", True))
        self.assertEqual(self.says("sudo npm publish --dry-run"), ("ask", False))
        # コマンド列の全体に適用する（lint が warn で言う形）
        self.assertEqual(self.says("npm publish; echo --dry-run"), ("ask", False))


class CommandLineTest(unittest.TestCase):
    """`--explain` が組み合わせ条件を出し、`--test-expectations` が when のルールを確かめる。"""

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
                    "deny": [VAULT],
                    "allow": [{"id": "all", "match": "Read", "glob": "*"}],
                },
                f,
            )

    def run_cli(self, *args: str) -> str:
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        environment.pop("CLAUDE_PROJECT_DIR", None)
        done = run_ccnavi(
            ["--root", self.root, *args],
            cwd=ROOT,
            env=environment,
        )
        return done.stdout

    def test_explain(self):
        out = self.run_cli("--explain")
        self.assertIn("when: any(glob '*.crt', all(glob '*/vault/*', not(glob '*.md')))", out)
        self.assertIn("        any\n          glob '*.crt'\n", out)

    def test_expectations(self):
        expectations = os.path.join(self.root, "expectations.yml")
        with open(expectations, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "deny": [
                        {"tool": "Write", "subject": "/repo/k.crt", "why": "キー"},
                        {"tool": "Write", "subject": "/repo/vault/token", "why": "秘密"},
                    ],
                    "ask": [
                        {"tool": "Write", "subject": "/repo/vault/README.md", "why": "説明"},
                    ],
                },
                f,
            )
        body = json.loads(self.run_cli("--test-expectations", expectations, "--json"))
        self.assertEqual(body["mismatches"], 0, body)
        hit = body["expectations"][0]["rules"][0]
        self.assertEqual(hit["id"], ".ccnavi/config/common/rules.yml:vault")
        self.assertEqual(hit["kind"], "when")


if __name__ == "__main__":
    unittest.main()
