"""シェルからワークスペースルート直下へ書く形を止めることの受入テスト。

Write / Edit はルールファイルの `workspace-root` が止める。シェルの書き込み先を解いた絶対パスを
同じルールに適用するので、`sed -i`・`>`・`tee`・`rm` などでも同じ置き場（`wip/handoff/`・
`scratchpad/`・`.claude/worktrees/`）だけが書ける。拒否の案内は置き場へ誘導する。
"""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import tempfile
import unittest

from ccnavi.hook import parentwrite
from ccnavi.infra import shellread
from tests import ROOT
from tests.inproc import run_ccnavi

RULE_ID = "builtin:guard-root-shell-write"
RULES = os.path.join(".ccnavi", "config", "common", "rules.yml")


class RootShellWriteTest(unittest.TestCase):
    def setUp(self):
        self.repo = os.path.realpath(tempfile.mkdtemp(prefix="ccnavi-rootwrite-"))
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        subprocess.run(["git", "init", "--quiet"], cwd=self.repo, check=True)
        os.makedirs(os.path.join(self.repo, ".ccnavi", "config", "common"))
        shutil.copy(os.path.join(ROOT, RULES), os.path.join(self.repo, RULES))
        for place in ("wip/handoff", "scratchpad", ".claude/worktrees/wt"):
            os.makedirs(os.path.join(self.repo, *place.split("/")))

    def decide(self, command, cwd=""):
        """(判定, 理由)。ルートの決まりで止めたときだけ判定が deny になる。"""
        body = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "session_id": "s1",
            "cwd": os.path.join(self.repo, cwd) if cwd else self.repo,
        }
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        result = run_ccnavi(
            ["--root", self.repo, "--mode", "enable", "--guard-core-files", "enable"],
            input=json.dumps(body),
            cwd=ROOT,
            env=environment,
        )
        if not result.stdout.strip():
            return "", ""
        out = json.loads(result.stdout)["hookSpecificOutput"]
        return out.get("permissionDecision", ""), out.get("permissionDecisionReason", "")

    def stopped(self, command, cwd=""):
        decision, reason = self.decide(command, cwd)
        return decision == "deny" and RULE_ID in reason

    def test_writes_into_the_checkout_root_are_stopped(self):
        for command in (
            "sed -i 's/a/b/' README.md",
            "echo x > README.md",
            "echo x >> docs/notes.md",
            "printf x | tee README.md",
            "rm -f README.md",
            "cp a.txt b.txt",
            "mv a.txt scratchpad/../b.txt",
            "rm docs/*.md",
            f"echo x > {self.repo}/README.md",
        ):
            with self.subTest(command=command):
                self.assertTrue(self.stopped(command))

    def test_the_places_for_drafts_and_worktrees_are_open(self):
        for command in (
            "echo x > wip/handoff/a.md",
            "sed -i 's/a/b/' scratchpad/a.txt",
            "tee scratchpad/a.txt < /dev/null",
            "cp a.txt wip/handoff/a.txt",
            "rm -f scratchpad/*",
            "echo x > .claude/worktrees/wt/README.md",
            f"echo x > {self.repo}/scratchpad/a.txt",
        ):
            with self.subTest(command=command):
                self.assertFalse(self.stopped(command))

    def test_writes_outside_the_root_and_fd_redirects_pass(self):
        for command in ("echo x > /dev/null", "echo x > /tmp/out.txt", "ls 2>&1 | head", "cat a"):
            with self.subTest(command=command):
                self.assertFalse(self.stopped(command))

    def test_the_cwd_decides_where_a_relative_target_lands(self):
        self.assertFalse(self.stopped("sed -i 's/a/b/' README.md", cwd=".claude/worktrees/wt"))
        self.assertFalse(self.stopped("echo x > README.md", cwd="scratchpad"))
        self.assertFalse(self.stopped("cd .claude/worktrees/wt && echo x > README.md"))
        self.assertTrue(self.stopped("cd docs && echo x > README.md"))

    def test_a_destination_that_is_the_root_itself_is_stopped(self):
        for command in (
            "cp scratchpad/a .",
            "cp -t . scratchpad/a",
            "install --target-directory=. x",
            "mv scratchpad/a ./",
            "cp x ..",
        ):
            cwd = "scratchpad" if command.endswith("..") else ""
            with self.subTest(command=command):
                self.assertTrue(self.stopped(command, cwd=cwd))
        self.assertFalse(self.stopped("cp x .", cwd="scratchpad"))

    def test_an_unreadable_cd_does_not_hide_a_relative_write(self):
        for command in (
            'cd "$PWD" && echo x > README.md',
            "d=.; cd $d; echo x > README.md",
            'cd "$(pwd)"; cp scratchpad/a .',
        ):
            with self.subTest(command=command):
                self.assertTrue(self.stopped(command))
        self.assertFalse(self.stopped('cd "$PWD" && echo x > a', cwd="scratchpad"))

    def test_output_options_of_common_downloaders_are_writes(self):
        for command in (
            "curl -o README.md https://example.com",
            "curl --output=README.md https://example.com",
            "wget -O README.md https://example.com",
            "sort -o README.md README.md",
        ):
            with self.subTest(command=command):
                self.assertTrue(self.stopped(command))
        self.assertFalse(self.stopped("curl -o scratchpad/x https://example.com"))

    def test_the_refusal_points_to_the_drafts_places(self):
        decision, reason = self.decide("sed -i 's/a/b/' README.md")
        self.assertEqual("deny", decision)
        self.assertIn("DENY_SHELL_WORKSPACE_ROOT", reason)
        self.assertIn("wip/handoff/", reason)
        self.assertIn("scratchpad/", reason)

    def test_unreadable_targets_are_left_to_the_post_check(self):
        for command in ('echo x > "$FILE"', "echo x > ~/a.txt", "echo x > `pwd`/a"):
            with self.subTest(command=command):
                self.assertFalse(self.stopped(command))

    GIT = "sh .ccnavi/scripts/ccnavi-git.sh"

    def test_引用符の中の山括弧は書き込みではない(self):
        for command in (
            f"{self.GIT} log -n 2 --format='%H%n%an <%ae>%n%B---'",
            f'{self.GIT} commit -m "feat: x\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n'
            'Claude-Session: https://claude.ai/code/session_x"',
            f"{self.GIT} log --format='%an <%ae>'",
            f'{self.GIT} commit -m "a" -m "Co-Authored-By: C <n@a.com>"',
            f'cd .claude/worktrees/wt && {self.GIT} commit -m "x <a@b>\nClaude-Session: s"',
            f"cd docs && {self.GIT} log --format='<%an>'",
            "echo '<a' 'b>' 'c<d>e' \"x>\" \"<y\"",
            "echo \"a > b\" 'c >> d'",
            f"{self.GIT} commit -m \"$(echo 'x <y@z>')\"",
            "echo \"$(echo '<a>' > scratchpad/o.txt)\"",
        ):
            with self.subTest(command=command):
                self.assertFalse(self.stopped(command))

    def test_引用符の外の本物のリダイレクトの判定は変わらない(self):
        for command, expected in (
            ("echo x > f", True),
            ("echo x >> f", True),
            ('echo "a <b>" > f', True),
            (f"{self.GIT} log --format='<%an>' > f", True),
            ("cd docs && echo '<a>' >> f", True),
            ("echo x 2>&1", False),
            ("echo x > /dev/null 2>&1", False),
            ("echo x > scratchpad/f", False),
            ("echo '<a>' > scratchpad/f", False),
        ):
            with self.subTest(command=command):
                self.assertEqual(self.stopped(command), expected)

    def test_演算子が末尾の語でも落ちず書き込み先に数えない(self):
        for command in ("echo x >", "echo x >>", "echo x 2>", "echo x <", "cd docs && echo x >"):
            with self.subTest(command=command):
                self.assertFalse(self.stopped(command))
                self.assertNotIn("internal", self.decide(command)[1].lower())

    def test_拒否の文面は書き込み先を取り出した元のコマンドを添える(self):
        _, reason = self.decide("cd docs && echo x > README.md")
        self.assertIn("書き込み先: README.md", reason)
        self.assertIn("読んだ箇所: echo x > README.md", reason)

    def test_引用符と演算子の乱れた組み合わせで例外を出さない(self):
        rng = random.Random(554)
        pieces = [
            "'",
            '"',
            "<",
            ">",
            ">>",
            "2>&1",
            " ",
            " ",
            "a",
            "b c",
            "&&",
            ";",
            "(",
            ")",
            "cd x",
            "echo",
            "$(",
            "`",
        ]
        for _ in range(400):
            command = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 14)))
            with self.subTest(command=command):
                reading = shellread.read(command)
                layers = [reading.unwrapped, reading.moved]
                parentwrite.write_targets(self.repo, command, layers)
                self.decide(command)


if __name__ == "__main__":
    unittest.main()
