"""ccnavi 自身の保護と認証情報の保護が組み込みにあることの受入テスト（実行前）。

止める・通すとヒットしたルールの並びの表は、Go の go/internal/hook/judge
（named_guards_test.go・credentials_test.go）に移した。対象は、4 種類のツリー、
直下の `wip/handoff/` の例外、自己防衛を切ったとき、共通のルールファイルが読めない・無い間、
認証情報、承認済みチケット。
ここに残すのは、文面（差し込みの目印が残らない・空白を含むルートで引用される・
案内するのはワークスペースの sh）と、ハードリンクの実ファイルを作る形。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from tests import ROOT, common_path, script_command
from tests.inproc import run_ccnavi

HOOKS = "builtin:guard-hooks"
SCRIPTS = "builtin:guard-scripts"
CONFIG = "builtin:guard-ccnavi-config"
HOME = "builtin:guard-project-home"
COMMON = "builtin:guard-common-layer"
LINKS = "builtin:guard-handoff-links"

# テスト用の最小の共通ルール。ここにある deny は、この検査のどの入力にもヒットしない。
MINIMAL_RULES = {
    "version": 1,
    "deny": [
        {
            "id": "blocked-fetch",
            "match": "WebFetch",
            "glob": "*://blocked.example/*",
            "message": "取りに行かない。",
        }
    ],
}


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


class Workspace(unittest.TestCase):
    prefix = "ccnavi-named-"

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix=self.prefix))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        write(common_path(self.root, "rules"), json.dumps(MINIMAL_RULES))
        self.log = os.path.join(self.root, "logs", "decisions.jsonl")

    def at(self, rel):
        return os.path.join(self.root, *rel.split("/")) if rel else self.root

    def hook(self, tool, tool_input, *, guard="enable", cwd=None, mode="enable", tickets=None):
        """(判定, 理由, ヒットしたルールの id の並び)。"""
        body = {
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "tool_input": tool_input,
            "session_id": "s1",
            "cwd": self.root if cwd is None else cwd,
        }
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        environment.pop("CLAUDE_PROJECT_DIR", None)
        if tickets is not None:
            environment["CCNAVI_TICKET_CONTROL"] = tickets
        result = run_ccnavi(
            ["--root", self.root, "--mode", mode, "--guard-core-files", guard],
            input=json.dumps(body),
            cwd=ROOT,
            env=environment,
        )
        if not result.stdout.strip():
            return "", "", self.last_ids()
        out = json.loads(result.stdout).get("hookSpecificOutput", {})
        return (
            out.get("permissionDecision", ""),
            out.get("permissionDecisionReason") or "",
            self.last_ids(),
        )

    def last_ids(self):
        try:
            with open(self.log, encoding="utf-8") as f:
                lines = [line for line in f if line.strip()]
        except OSError:
            return []
        return json.loads(lines[-1]).get("rules", []) if lines else []

    def write_to(self, rel, **kw):
        return self.hook("Write", {"file_path": self.at(rel), "content": "x\n"}, **kw)


class NamedGuardsTest(Workspace):
    prefix = "ccnavi-named-pre-"

    def test_a_hard_linked_copy_is_not_written(self):
        write(self.at(".claude/hooks/guard.sh"), "#!/bin/sh\n")
        os.makedirs(self.at("wip/handoff/.claude/hooks"))
        try:
            os.link(self.at(".claude/hooks/guard.sh"), self.at("wip/handoff/.claude/hooks/g.sh"))
        except OSError:
            self.skipTest("ハードリンクを作れない機械")
        decision, _, ids = self.write_to("wip/handoff/.claude/hooks/g.sh")
        self.assertEqual(decision, "deny")
        self.assertEqual(ids, [HOOKS, LINKS])

    def test_the_named_rules_are_not_added_twice(self):
        # 通常時は add_rules の後に 1 度だけ足す。同じ id が 2 本入ると、同じ文面が併記される。
        _, reason, ids = self.write_to(".claude/hooks/guard.sh")
        self.assertEqual(ids, [HOOKS])
        self.assertEqual(reason.count("hook 本体です"), 1)


class UnreadableRulesTest(Workspace):
    """共通のルールファイルが読めない間と、無い間。4 本は外れず、直し方は下書きになる。"""

    prefix = "ccnavi-named-broken-"
    DRAFT = "wip/handoff/.ccnavi/config/common/rules.yml"

    def broken(self):
        write(common_path(self.root, "rules"), "deny:\n  - id: [unclosed\n<<<<<<< HEAD\n")

    def absent(self):
        os.remove(common_path(self.root, "rules"))

    def test_the_common_rules_cannot_be_repaired_by_write_or_edit(self):
        self.broken()
        for tool, tool_input in (
            ("Write", {"file_path": self.at(".ccnavi/config/common/rules.yml"), "content": "a"}),
            (
                "Edit",
                {
                    "file_path": self.at(".ccnavi/config/common/rules.yml"),
                    "old_string": "a",
                    "new_string": "b",
                },
            ),
            ("NotebookEdit", {"notebook_path": self.at(".ccnavi/config/common/rules.yml")}),
        ):
            with self.subTest(tool=tool):
                decision, reason, ids = self.hook(tool, tool_input)
                self.assertEqual(decision, "deny", reason)
                self.assertEqual(ids[0], CONFIG)
                # 下書きに置いてユーザに取り込みを頼む、と言う。
                self.assertIn(self.DRAFT, reason)
                self.assertIn("ccnavi-user-handoff.sh", reason)

    def test_the_shell_cannot_break_or_fix_the_rules_while_they_are_broken(self):
        self.broken()
        decision, reason, ids = self.hook(
            "Bash", {"command": "echo x > .ccnavi/config/common/rules.yml"}
        )
        self.assertEqual(decision, "deny")
        self.assertIn("builtin:guard-config-via-bash", ids)
        # 「Write か Edit で書いてください」とは言わない。下書きとユーザの取り込みを案内する。
        self.assertNotIn("Write か Edit のツールで書いて", reason)
        self.assertIn(self.DRAFT, reason)
        self.assertIn("ccnavi-user-handoff.sh", reason)


class ApprovedPreTest(Workspace):
    """承認済みの置き場は、チケット制御の保護。自己防衛の値に依らず、実行前は拒否する。"""

    prefix = "ccnavi-named-approved-"
    DOING = ".ccnavi/approved/doing/i0001.md"
    APPROVED = "builtin:ticket-approved"

    def test_the_message_guides_status_finish_and_propose(self):
        _, reason, _ = self.write_to(self.DOING, tickets="enable")
        ticket = script_command(self.root, "ccnavi-ticket.sh")
        for text in (
            f"{ticket} status",
            f"{ticket} finish <識別子>",
            f"{ticket} approval-request <識別子>",
            "ccnavi-user-agree.sh",
            "wip/proposals/todo/",
        ):
            self.assertIn(text, reason)
        self.assertNotIn("{root}", reason)
        self.assertNotIn("{script:", reason)


class MessageTest(Workspace):
    """文面に差し込みの目印が残らず、案内がワークスペースの sh を指す。"""

    prefix = "ccnavi named with space-"

    def reasons(self):
        out = []
        for rel in (
            ".claude/hooks/guard.sh",
            ".claude/settings.json",
            ".ccnavi/scripts/ccnavi-x.sh",
            ".ccnavi/config/common/rules.yml",
        ):
            out.append((rel, self.write_to(rel)[1]))
        return out

    def test_no_placeholder_is_left(self):
        for rel, reason in self.reasons():
            with self.subTest(path=rel):
                self.assertNotIn("{root}", reason)
                self.assertNotIn("{script:", reason)

    def test_the_root_with_a_space_is_quoted(self):
        self.assertIn(" ", self.root)
        stage = script_command(self.root, "ccnavi-stage.sh")
        self.assertIn('"', stage)
        for rel, reason in self.reasons():
            with self.subTest(path=rel):
                self.assertIn(stage, reason)

    def test_the_guide_points_to_the_workspace_script(self):
        stage = script_command(self.root, "ccnavi-stage.sh")
        self.assertTrue(stage.endswith('/.ccnavi/scripts/ccnavi-stage.sh"'))
        self.assertIn(os.path.realpath(self.root).replace("\\", "/"), stage)


if __name__ == "__main__":
    unittest.main()
