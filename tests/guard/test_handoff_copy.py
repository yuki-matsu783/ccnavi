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


if __name__ == "__main__":
    unittest.main()
