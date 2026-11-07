"""不正なチケットがあっても、PreToolUse は異常終了せずに、無いときと同じ判定を返す。

hook が非 0 で終わると Claude Code は判定を読まないので、止めるはずの呼び出しが通る。
不正な YAML の読み方そのものは `go/internal/infra/yaml` の go test が見る。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from tests import ROOT, common_path
from tests.inproc import run_ccnavi


class BrokenTicketHookTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def workspace(self, name: str, ticket: str | None) -> str:
        root = os.path.join(self.dir.name, name)
        rules = common_path(root, "rules")
        os.makedirs(os.path.dirname(rules))
        shutil.copyfile(os.path.join(ROOT, "tests", "fixtures", "rules.yml"), rules)
        if ticket is not None:
            review = os.path.join(root, "wip", "proposals", "review")
            os.makedirs(review)
            with open(os.path.join(review, "T-1.md"), "w", encoding="utf-8") as f:
                f.write(
                    f"---\nticket: {ticket}\ntitle: x\n"
                    "ccnavi_approved:\n  approved_at: 2026-01-01\n---\nbody\n"
                )
        return root

    def decision(self, root: str) -> str:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin main"},
            "session_id": "s1",
            "cwd": root,
        }
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        env.pop("CLAUDE_PROJECT_DIR", None)
        env.update(CCNAVI_LEARNING="disable", CCNAVI_GUARD_CORE_FILES="disable")
        done = run_ccnavi(
            ["--root", root, "--mode", "enable"], input=json.dumps(payload), cwd=ROOT, env=env
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)["hookSpecificOutput"].get("permissionDecision")

    def test_git_push_is_still_denied(self):
        self.assertEqual(self.decision(self.workspace("clean", None)), "deny")
        for name, ticket in (("int-tab", "!!int \t"), ("int-empty", "!!int")):
            with self.subTest(ticket=name):
                self.assertEqual(self.decision(self.workspace(name, ticket)), "deny")


if __name__ == "__main__":
    unittest.main()
