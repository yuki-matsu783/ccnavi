"""下書きの置き場（`wip/handoff/`）を足場にして、守る場所へ書く形を止めることの受入テスト
（src/ccnavi/policy/selfguard_shell.py の `builtin:guard-handoff-links`・
`builtin:guard-handoff-ln`）。

1. 名指しのツール: 利用者がルールファイルに書く保護は、置き場の中を `except` の式で外せる。
   式はファイルを見ないので、守る場所とハードリンクでつながった写しへの Write も外れる。
   組み込みの 1 本が、置き場の中のつながった対象だけを止める。ふつうの写しへの Write は止めない
   （ccnavi 自身の保護の `builtin:guard-hooks` などは、つながった写しを外さない）
2. シェル: 置き場の中にリンク（シンボリックリンク・ハードリンク）を作る `ln`・`link`・
   リンクを作る `cp` を止める。置き場の外のリンクと、ふつうの `cp` は止めない
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from tests import ROOT, common_path, requires_symlink
from tests.inproc import run_ccnavi

LINKS = "builtin:guard-handoff-links"
MINIMAL_RULES = {
    "version": 1,
    "deny": [
        {
            "id": "blocked-fetch",
            "match": "WebFetch",
            "glob": "*://blocked.example/*",
            "message": "x",
        }
    ],
}
LN = "builtin:guard-handoff-ln"


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


class HandoffLinksTest(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="ccnavi-handoff-links-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        # 共通のルールはテスト用の最小の組にする。ccnavi 自身の保護は組み込み
        # （builtin:guard-hooks など。書き込みの例外は handoff_write_exempt）にあり、
        # ルールファイルに依らない。
        write(common_path(self.root, "rules"), json.dumps(MINIMAL_RULES))
        write(self.at(".claude/hooks/guard.sh"), "#!/bin/sh\n")
        write(self.at(".claude/settings.json"), "{}\n")
        self.pad = self.at("wip/handoff")
        os.makedirs(self.pad)

    def at(self, rel):
        return os.path.join(self.root, *rel.split("/"))

    def hook(self, tool, tool_input, cwd=None):
        body = {
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "tool_input": tool_input,
            "session_id": "s1",
            "cwd": self.root if cwd is None else cwd,
        }
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment.pop("CLAUDE_PROJECT_DIR", None)
        result = run_ccnavi(
            ["--root", self.root, "--mode", "enable"],
            input=json.dumps(body),
            cwd=ROOT,
            env=environment,
        )
        if not result.stdout.strip():
            return "", ""
        out = json.loads(result.stdout).get("hookSpecificOutput", {})
        return out.get("permissionDecision", ""), out.get("permissionDecisionReason") or ""

    def write_to(self, rel):
        return self.hook("Write", {"file_path": self.at(rel), "content": "x\n"})

    def link(self, source, rel):
        os.makedirs(os.path.dirname(self.at(rel)), exist_ok=True)
        try:
            os.link(self.at(source), self.at(rel))
        except OSError:
            self.skipTest("ハードリンクを作れない機械")

    def test_a_hard_linked_copy_of_a_hook_is_not_written(self):
        self.link(".claude/hooks/guard.sh", "wip/handoff/.claude/hooks/guard.sh")
        decision, reason = self.write_to("wip/handoff/.claude/hooks/guard.sh")
        self.assertEqual(decision, "deny", reason)
        self.assertIn(LINKS, reason)

    def test_a_hard_linked_copy_of_the_settings_is_not_written(self):
        self.link(".claude/settings.json", "wip/handoff/.claude/settings.json")
        for tool, tool_input in (
            ("Write", {"file_path": self.at("wip/handoff/.claude/settings.json"), "content": "{}"}),
            (
                "Edit",
                {
                    "file_path": self.at("wip/handoff/.claude/settings.json"),
                    "old_string": "{}",
                    "new_string": "{ }",
                },
            ),
        ):
            with self.subTest(tool=tool):
                decision, reason = self.hook(tool, tool_input)
                self.assertEqual(decision, "deny", reason)
                self.assertIn(LINKS, reason)

    def test_a_hard_link_inside_a_worktree_or_a_project_is_not_written(self):
        for tree in (".claude/worktrees/a", "projects/lib"):
            rel = f"{tree}/wip/handoff/.claude/hooks/guard.sh"
            with self.subTest(tree=tree):
                self.link(".claude/hooks/guard.sh", rel)
                decision, reason = self.write_to(rel)
                self.assertEqual(decision, "deny", reason)

    @requires_symlink
    def test_a_symlinked_copy_is_not_written(self):
        os.makedirs(self.at("wip/handoff/.claude/hooks"))
        os.symlink(self.at(".claude/hooks/guard.sh"), self.at("wip/handoff/.claude/hooks/s.sh"))
        decision, reason = self.write_to("wip/handoff/.claude/hooks/s.sh")
        self.assertEqual(decision, "deny", reason)

    def test_a_plain_copy_and_a_new_draft_are_written(self):
        write(self.at("wip/handoff/.claude/hooks/guard.sh"), "#!/bin/sh\n")
        for rel in (
            "wip/handoff/.claude/hooks/guard.sh",
            "wip/handoff/.claude/hooks/new.sh",
            "wip/handoff/.claude/settings.json",
            "wip/handoff/.ccnavi/config/common/rules.yml",
            "wip/handoff/.ccnavi/docs/guide.md",
        ):
            with self.subTest(rel=rel):
                decision, reason = self.write_to(rel)
                self.assertNotEqual(decision, "deny", reason)

    def test_a_hard_link_outside_the_handoff_place_is_left_to_the_rules(self):
        write(self.at("scratchpad/a.txt"), "a\n")
        self.link("scratchpad/a.txt", "scratchpad/b.txt")
        _, reason = self.write_to("scratchpad/b.txt")
        self.assertNotIn(LINKS, reason)

    def test_making_a_link_in_the_handoff_place_is_stopped(self):
        for command, cwd in (
            ("ln -s ../../.claude/hooks/guard.sh wip/handoff/.claude/hooks/x.sh", None),
            ("ln .claude/hooks/guard.sh wip/handoff/.claude/hooks/x.sh", None),
            ("ln -sf /etc/passwd wip/handoff/p", None),
            ("cd wip/handoff && ln -s /etc/passwd p", None),
            ("/usr/bin/ln -s /etc/passwd wip/handoff/p", None),
            ("link /etc/hosts wip/handoff/h", None),
            ("cp -l .claude/hooks/guard.sh wip/handoff/x.sh", None),
            ("cp --symbolic-link /etc/hosts wip/handoff/h", None),
            ("cp -rs .claude wip/handoff/.claude", None),
            ("ln -s /etc/passwd p", self.pad),
        ):
            with self.subTest(command=command, cwd=cwd):
                os.makedirs(self.at("wip/handoff/.claude/hooks"), exist_ok=True)
                decision, reason = self.hook("Bash", {"command": command}, cwd=cwd)
                self.assertEqual(decision, "deny", reason)
                self.assertIn(LN, reason)

    def test_links_elsewhere_and_plain_copies_are_not_stopped_by_this_rule(self):
        for command in (
            "ln -s a b",
            "cp .ccnavi/docs/a.md wip/handoff/.ccnavi/docs/a.md",
            "cp -p README.md wip/handoff/README.md",
            "grep -rn handoff docs",
        ):
            with self.subTest(command=command):
                _, reason = self.hook("Bash", {"command": command})
                self.assertNotIn(LN, reason)


if __name__ == "__main__":
    unittest.main()
