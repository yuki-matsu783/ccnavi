"""ルールの `{!root}`（ワークスペースルートの外）を、hook として呼んだときの判定の受入テスト。

`{!root}` の展開（外に数えるものの表・長さの上限・書き方の誤り）は `go/internal/policy/rules` の
go test が見る。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from tests import ROOT, common_path
from tests.inproc import run_ccnavi

# 「ワークスペースルートの外」を書ける唯一の形。
NOT_ROOT = "^{!root}"


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def rules_file(directory: str, *rule: dict, section: str = "deny") -> str:
    """ルールファイルを 1 枚書いてパスを返す。

    置くのは共通レイヤーの既定の場所。hook として呼ぶ側は `--rules` を渡せない
    （診断でだけ有効な）ので、ワークスペースルートの下の既定のパスに要る。
    """
    return write(
        common_path(directory, "rules"),
        json.dumps({"version": 1, section: list(rule)}),
    )


def outside_rule(expression: str = NOT_ROOT, **extra) -> dict:
    rule = {
        "id": "outside-workspace",
        "match": "Write|Edit|NotebookEdit",
        "regex": expression,
        "message": "ワークスペースの外です。",
    }
    rule.update(extra)
    return rule


class NotRootJudgeTest(unittest.TestCase):
    """hook として呼んだときの判定。"""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = os.path.realpath(self.dir.name)
        self.rules = rules_file(self.dir.name, outside_rule())

    def judge(self, path: str, mode: str = "enable") -> dict:
        payload = json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Write",
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
                mode,
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

    def test_outside_is_denied_and_inside_is_not(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            outside = os.path.join(os.path.realpath(elsewhere), "draft.yml")
            out = self.judge(outside)
        self.assertEqual(out.get("permissionDecision"), "deny", out)
        self.assertIn("outside-workspace", out.get("permissionDecisionReason", ""))

        out = self.judge(os.path.join(self.root, "README.md"))
        self.assertNotEqual(out.get("permissionDecision"), "deny", out)

    # --- 観点 11 ---

    def test_dry_run_does_not_stop(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            outside = os.path.join(os.path.realpath(elsewhere), "draft.yml")
            out = self.judge(outside, mode="dry-run")
        self.assertNotEqual(out.get("permissionDecision"), "deny", out)
        # 「止まらない」だけを見ると、ルールがヒットしていない実装でも通る。
        # `enable` なら止めていたことを告げる文が出ていることまで縛る。
        self.assertIn("outside-workspace", json.dumps(out, ensure_ascii=False), out)


if __name__ == "__main__":
    unittest.main()
