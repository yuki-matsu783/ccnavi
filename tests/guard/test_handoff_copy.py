"""守る場所のファイルを、下書きの置き場（`wip/handoff/`）へ cp で写す形も止めることの受入テスト。

守る場所を直したいときは、`ccnavi-stage.sh <元のパス>` で置き場へ写し、写しを直す
（.ccnavi/docs/scratchpad.md）。シェルから書き込む形の保護は、cp の行き先の最後の語に
守る名前があれば止めるので、`cp .ccnavi/scripts/ccnavi-git.sh wip/handoff/ccnavi-git.sh`
も止まる。止めた文が `ccnavi-stage.sh` を案内することを確かめる。
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

RULE_ID = "builtin:guard-setting-files"

RULES = {
    "version": 1,
    "allow": [{"id": "anything-else", "match": "Bash|Read|Write|Edit", "regex": "."}],
}


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


class HandoffCopyTest(unittest.TestCase):
    def setUp(self):
        self.repo = os.path.realpath(tempfile.mkdtemp(prefix="ccnavi-scratchpad-"))
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        subprocess.run(["git", "init", "--quiet"], cwd=self.repo, check=True)
        write(
            os.path.join(self.repo, ".ccnavi", "config", "common", "rules.yml"), json.dumps(RULES)
        )
        write(os.path.join(self.repo, ".ccnavi", "scripts", "ccnavi-git.sh"), "#!/bin/sh\n")
        os.makedirs(os.path.join(self.repo, "wip", "handoff"))
        self.pad = os.path.join(self.repo, "wip", "handoff")

    def decide(self, command, cwd=None):
        """ヒットした組み込みの保護の id を返す。ヒットしなければ空。"""
        body = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "session_id": "s1",
            "cwd": self.repo if cwd is None else cwd,
        }
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        result = run_ccnavi(
            [
                "--root",
                self.repo,
                "--mode",
                "enable",
                "--guard-core-files",
                "enable",
            ],
            input=json.dumps(body),
            cwd=ROOT,
            env=environment,
        )
        self.said = result.stdout
        return RULE_ID if RULE_ID in result.stdout else ""

    def test_copying_into_the_scratchpad_is_stopped_and_points_to_the_stage_script(self):
        for command in (
            "cp .ccnavi/scripts/ccnavi-git.sh wip/handoff/ccnavi-git.sh",
            f"cp {self.repo}/.ccnavi/scripts/ccnavi-git.sh {self.pad}/ccnavi-git.sh",
            "cp -f -- .ccnavi/scripts/ccnavi-git.sh wip/handoff/ccnavi-git.sh",
            "install .ccnavi/scripts/ccnavi-git.sh wip/handoff/ccnavi-git.sh",
        ):
            with self.subTest(command=command):
                self.assertEqual(RULE_ID, self.decide(command))
                self.assertIn("ccnavi-stage.sh", self.said)
                self.assertIn("<元のパス>", self.said)

    def test_the_guard_itself_still_stops_writes(self):
        for command in (
            "echo x > .ccnavi/config/common/rules.yml",
            "sed -i s/a/b/ .ccnavi/config/common/rules.yml",
            "cp /tmp/x .ccnavi/config/common/rules.yml",
        ):
            with self.subTest(command=command):
                self.assertEqual(RULE_ID, self.decide(command))


GIT = "sh .ccnavi/scripts/ccnavi-git.sh "


class HandoffDeleteTest(unittest.TestCase):
    """置き場の中の写しの削除は通す。守る場所そのものと、置き場の外へ出る形は止める。"""

    decide = HandoffCopyTest.decide

    def setUp(self):
        HandoffCopyTest.setUp(self)
        write(os.path.join(self.pad, ".ccnavi", "docs", "x.md"), "x\n")
        write(os.path.join(self.repo, ".ccnavi", "docs", "x.md"), "x\n")

    def stopped(self, command, cwd=None):
        self.decide(command, cwd)
        return '"deny"' in self.said

    def forms(self, rel):
        """素の rm（絶対パス）とラッパーの rm（相対パス）の、同じ対象を消す 2 つの形。"""
        return (f"rm {os.path.join(self.repo, *rel.split('/'))}", GIT + f"rm {rel}")

    def test_deleting_a_copy_in_the_place_is_allowed(self):
        copy = "wip/handoff/.ccnavi/docs/x.md"
        absolute = f"{self.pad}/.ccnavi/docs/x.md"
        for command in (
            f"rm {absolute}",
            f"rm -f -- {absolute}",
            f"rm -r {self.pad}/.ccnavi",
            f"rm {self.repo}/WIP/Handoff/.ccnavi/docs/x.md",
            f"rm {absolute} && echo done",
            GIT + f"rm {copy}",
            GIT + f"rm -f {copy}",
            GIT + "rm WIP/Handoff/.ccnavi/docs/x.md",
            GIT + f"rm {absolute}",
        ):
            with self.subTest(command=command):
                self.assertFalse(self.stopped(command), self.said)

    def test_a_relative_plain_rm_is_stopped(self):
        # 素の rm は絶対パスだけ外す（sh -c や find -execdir の中の cd が見えない）。
        for command in (
            "rm wip/handoff/.ccnavi/docs/x.md",
            "sh -c 'cd /tmp && rm wip/handoff/.ccnavi/docs/x.md'",
            "find /tmp -maxdepth 0 -execdir rm wip/handoff/.ccnavi/docs/x.md ;",
        ):
            with self.subTest(command=command):
                self.assertTrue(self.stopped(command), self.said)

    def test_deleting_the_real_files_is_stopped(self):
        copy = f"{self.pad}/.ccnavi/docs/x.md"
        for command in (
            "rm .ccnavi/docs/x.md",
            f"rm {copy} .ccnavi/docs/x.md",
            f"rm {copy} && rm .ccnavi/docs/x.md",
            f"rm {copy} > .ccnavi/docs/x.md",
            "cd .ccnavi && rm docs/x.md",
            GIT + "rm .ccnavi/docs/x.md",
            GIT + "rm wip/handoff/.ccnavi/docs/x.md .ccnavi/docs/x.md",
            GIT + "rm wip/handoff/.ccnavi/docs/x.md && " + GIT + "rm .ccnavi/docs/x.md",
        ):
            with self.subTest(command=command):
                self.assertTrue(self.stopped(command), self.said)

    def test_a_home_or_expanded_word_is_stopped_even_inside_the_place(self):
        for command in (
            "rm ~/.claude/settings.json",
            "rm ~/.ccnavi/docs/x.md",
            "rm ~root/.ccnavi/docs/x.md",
            GIT + "rm ~/.claude/settings.json",
            GIT + "rm ~/.ccnavi/docs/x.md",
            GIT + "rm ~root/.ccnavi/docs/x.md",
        ):
            with self.subTest(command=command):
                self.assertTrue(self.stopped(command, self.pad), self.said)

    def test_a_glob_is_stopped(self):
        for rel in ("wip/handoff/.ccnavi/docs/*", "wip/handoff/.ccnavi/docs/x.m?"):
            for command in self.forms(rel):
                with self.subTest(command=command):
                    self.assertTrue(self.stopped(command), self.said)

    def test_a_place_not_right_under_the_tree_or_leaving_it_is_stopped(self):
        for rel in (
            "docs/wip/handoff/.ccnavi/docs/x.md",
            "wip/handoff/../.ccnavi/docs/x.md",
            "wip/handoff/../../.ccnavi/docs/x.md",
            "wip/handoffx/.ccnavi/docs/x.md",
        ):
            for command in self.forms(rel):
                with self.subTest(command=command):
                    self.assertTrue(self.stopped(command), self.said)

    def test_a_linked_copy_is_stopped(self):
        real = os.path.join(self.repo, ".ccnavi", "docs", "x.md")
        os.makedirs(os.path.join(self.pad, ".ccnavi", "docs"), exist_ok=True)
        hard = os.path.join(self.pad, ".ccnavi", "docs", "hard.md")
        try:
            os.link(real, hard)
        except OSError:
            self.skipTest("ハードリンクを作れない機械")
        for command in self.forms("wip/handoff/.ccnavi/docs/hard.md"):
            with self.subTest(command=command):
                self.assertTrue(self.stopped(command), self.said)
        os.remove(hard)
        shutil.rmtree(os.path.join(self.pad, ".ccnavi"))
        try:
            os.symlink(os.path.join(self.repo, ".ccnavi"), os.path.join(self.pad, ".ccnavi"))
        except OSError:
            self.skipTest("シンボリックリンクを作れない機械")
        for command in self.forms("wip/handoff/.ccnavi/docs/x.md"):
            with self.subTest(command=command):
                self.assertTrue(self.stopped(command), self.said)

    def test_writing_into_the_place_is_still_stopped(self):
        for command in (
            "cp .ccnavi/docs/x.md wip/handoff/.ccnavi/docs/x.md",
            "mv /tmp/a wip/handoff/.ccnavi/docs/x.md",
            "echo x > wip/handoff/.ccnavi/docs/x.md",
        ):
            with self.subTest(command=command):
                self.assertEqual(RULE_ID, self.decide(command))


if __name__ == "__main__":
    unittest.main()
