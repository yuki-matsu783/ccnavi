"""守る場所のファイルを、下書きの置き場（`wip/handoff/`）へコピーする cp を通すことの受入テスト。

守る場所を直したいときは、置き場へコピーしてコピーを直す（.ccnavi/docs/scratchpad.md）。シェルから
書き込む形の保護は、cp の行き先の最後の語に守る名前があれば止めるので、`cp
.ccnavi/scripts/ccnavi-git.sh wip/handoff/ccnavi-git.sh` のように同じ名前でコピーする形まで止まる。
その形だけを通し、置き場を足場にして守る場所へ書く形は通さないことを確かめる。

保護の式に否定先読みを足して通すと、ルールの読み込みが先読みを受け付けず、
保護の 1 本がまるごと外れて `echo x > .ccnavi/config/common/rules.yml` まで通る。
`test_the_guard_itself_still_stops_writes` はそれを見る。
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
        return RULE_ID if RULE_ID in result.stdout else ""

    def test_copying_into_the_scratchpad_under_the_same_name_passes(self):
        for command in (
            "cp .ccnavi/scripts/ccnavi-git.sh wip/handoff/ccnavi-git.sh",
            f"cp {self.repo}/.ccnavi/scripts/ccnavi-git.sh {self.pad}/ccnavi-git.sh",
            "cp .ccnavi/scripts/ccnavi-git.sh wip/handoff/",
            "cp -f -- .ccnavi/scripts/ccnavi-git.sh wip/handoff/ccnavi-git.sh",
            "install .ccnavi/scripts/ccnavi-git.sh wip/handoff/ccnavi-git.sh",
        ):
            with self.subTest(command=command):
                self.assertEqual("", self.decide(command))

    def test_a_relative_destination_is_read_from_the_cwd(self):
        sub = os.path.join(self.repo, "docs")
        os.makedirs(sub)
        # docs/ から見た wip/handoff/ は置き場ではない。
        self.assertEqual(RULE_ID, self.decide("cp /tmp/x wip/handoff/ccnavi-git.sh", cwd=sub))
        self.assertEqual("", self.decide(f"cp /tmp/x {self.pad}/ccnavi-git.sh", cwd=sub))

    def test_the_scratchpad_is_not_a_stepping_stone(self):
        try:
            os.symlink(
                os.path.join(self.repo, ".ccnavi"),
                os.path.join(self.pad, "link"),
                target_is_directory=True,
            )
        except OSError:
            # Windows で開発者モードでないと作れない。
            self.skipTest("シンボリックリンクを作れない機械")
        for command in (
            # `..` で置き場の外へ出る
            "cp /tmp/x wip/handoff/../../.ccnavi/config/common/rules.yml",
            # 置き場の中のリンクを通って守る場所へ書く
            "cp /tmp/x wip/handoff/link/scripts/ccnavi-git.sh",
            # リンクを作る・リンクのままコピーする・行き先を -t で動かす
            "cp -l .ccnavi/scripts/ccnavi-git.sh wip/handoff/ccnavi-git.sh",
            "cp -s .ccnavi wip/handoff/.ccnavi",
            "cp -a .ccnavi wip/handoff/.ccnavi",
            "ln -s .ccnavi/scripts/ccnavi-git.sh wip/handoff/ccnavi-git.sh",
            "cp -t .ccnavi/scripts /tmp/x wip/handoff/ccnavi-git.sh",
            # 長い選択肢の省略（GNU は曖昧でない範囲で受ける）と、知らない選択肢
            "cp --target .ccnavi/scripts /tmp/x wip/handoff/ccnavi-git.sh",
            "cp --target-d .ccnavi/scripts /tmp/x wip/handoff/ccnavi-git.sh",
            "cp --target=.ccnavi/scripts wip/handoff/ccnavi-git.sh",
            "cp --l .ccnavi/scripts/ccnavi-git.sh wip/handoff/ccnavi-git.sh",
            "cp --sym .ccnavi wip/handoff/ccnavi-git.sh",
            "cp --rec .ccnavi wip/handoff/ccnavi-git.sh",
            "install --dir .ccnavi wip/handoff/ccnavi-git.sh",
            "cp -x .ccnavi/scripts/ccnavi-git.sh wip/handoff/ccnavi-git.sh",
            # 1 本目で置き場へコピーし、続くコマンドで守る場所へ書く
            "cp a wip/handoff/ccnavi-git.sh && cp /tmp/x .ccnavi/config/common/rules.yml",
            "cp a wip/handoff/ccnavi-git.sh; echo x > .ccnavi/config/common/rules.yml",
            "cp a wip/handoff/ccnavi-git.sh > .ccnavi/config/common/rules.yml",
            # 表記どおりに読めない行き先
            "cp $X wip/handoff/ccnavi-git.sh",
            # 名前が似ているだけの別の場所
            "cp /tmp/x wip/handoffx/ccnavi-git.sh",
        ):
            with self.subTest(command=command):
                self.assertEqual(RULE_ID, self.decide(command))

    def test_a_home_destination_is_not_read_from_the_cwd(self):
        # シェルは `~` をホームに展開する。cwd が置き場の中でも、置き場の中とは読まない。
        self.assertEqual(RULE_ID, self.decide("cp /tmp/x ~/.claude/settings.json", cwd=self.pad))

    def test_a_hard_linked_destination_is_not_exempt(self):
        # 置き場のコピーが守る場所のファイルとつながっていれば、上書きは守る場所も書き換える。
        original = os.path.join(self.repo, ".ccnavi", "scripts", "ccnavi-git.sh")
        try:
            os.link(original, os.path.join(self.pad, "ccnavi-git.sh"))
        except OSError:
            self.skipTest("ハードリンクを作れない機械")
        self.assertEqual(RULE_ID, self.decide("cp /tmp/x wip/handoff/ccnavi-git.sh"))

    def test_a_linked_scratchpad_is_not_exempt(self):
        # 置き場そのものが守る場所へのリンクなら、置き場へコピーすることが守る場所へ書くことになる。
        shutil.rmtree(self.pad)
        try:
            os.symlink(
                os.path.join(self.repo, ".ccnavi", "scripts"), self.pad, target_is_directory=True
            )
        except OSError:
            self.skipTest("シンボリックリンクを作れない機械")
        self.assertEqual(RULE_ID, self.decide("cp /tmp/x wip/handoff/ccnavi-git.sh"))

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
