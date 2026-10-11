"""`ccnavi ticket new`。親の提案の雛形を作り、フェーズの scope を allow に写す。"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

import yaml

from tests.inproc import run_ccnavi

PHASES = """version: 1
phases:
  design:
    kind: work
    title: 設計
    review: mr
    scope: ["wip/design/*", "docs/*"]
  implement:
    kind: work
    title: 実装
    review: mr
    scope: ["src/*", "docs/*"]
  quick:
    kind: work
    title: 小さい作業
    review: mr
    scope: inherit
  fix-up:
    kind: feedback
    title: 対応
    review: mr
    scope: inherit
"""


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class NewTicketTest(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="ccnavi-new-ticket-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        os.makedirs(os.path.join(self.ws, ".claude"))
        config = os.path.join(self.ws, ".ccnavi", "config")
        os.makedirs(config)
        with open(os.path.join(config, "phases.yml"), "w", encoding="utf-8") as f:
            f.write(PHASES)
        git(self.ws, "init", "--quiet", "-b", "main")
        git(
            self.ws,
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "--allow-empty",
            "-m",
            "i",
        )
        self.tree = self.add_worktree("feature-1-demo")

    def add_worktree(self, name):
        path = os.path.join(self.ws, ".claude", "worktrees", name)
        git(self.ws, "worktree", "add", "--quiet", path, "-b", name)
        return path

    def new(self, *args, cwd=None):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment.pop("CLAUDE_PROJECT_DIR", None)
        return run_ccnavi(
            ["--root", self.ws, "--cwd", cwd or self.tree, "ticket", "new", *args],
            input="",
            env=environment,
        )

    def front(self, name):
        path = os.path.join(self.tree, "wip", "proposals", "todo", name + ".md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        return yaml.safe_load(text.split("---\n")[1])

    def test_scopes_are_collected_without_duplicates(self):
        result = self.new("feature", "demo", "--title", "題", "--plan", "design,implement")
        self.assertEqual(0, result.returncode, result.stderr)
        front = self.front("feature-1-demo")
        self.assertEqual(
            ["wip/design/*", "docs/*", "src/*"], [entry["glob"] for entry in front["allow"]]
        )
        self.assertEqual({"design", "implement"}, set(front["phases"]))
        self.assertEqual(["design", {"type": "implement", "after": [1]}], front["plan"])

    def test_issue_number_is_used(self):
        self.tree = self.add_worktree("fix-77-demo")
        result = self.new(
            "fix", "demo", "--title", "t", "--plan", "implement", "--issue", "77", cwd=self.tree
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(77, self.front("fix-77-demo")["issue"])

    def test_inherit_only_needs_allow(self):
        result = self.new("feature", "demo", "--title", "t", "--plan", "quick")
        self.assertEqual(1, result.returncode)
        self.assertIn("--allow", result.stderr)
        result = self.new(
            "feature", "demo", "--title", "t", "--plan", "quick", "--allow", "src/x/*"
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("src/x/*", self.front("feature-1-demo")["allow"][0]["glob"])

    def test_feedback_definition_is_refused(self):
        result = self.new("feature", "demo", "--title", "t", "--plan", "fix-up")
        self.assertEqual(1, result.returncode)
        self.assertIn("feedback", result.stderr)

    def test_unknown_definition_is_refused(self):
        result = self.new("feature", "demo", "--title", "t", "--plan", "nothing")
        self.assertEqual(1, result.returncode)
        self.assertIn("nothing", result.stderr)

    def test_main_tree_is_refused(self):
        result = self.new("feature", "demo", "--title", "t", "--plan", "design", cwd=self.ws)
        self.assertEqual(1, result.returncode)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "wip", "proposals")))

    def test_existing_proposal_is_not_overwritten(self):
        args = ("feature", "demo", "--title", "t", "--plan", "design", "--issue", "1")
        self.assertEqual(0, self.new(*args).returncode)
        again = self.new(*args)
        self.assertEqual(1, again.returncode)

    def test_generated_proposal_passes_lint(self):
        self.assertEqual(
            0, self.new("feature", "demo", "--title", "題", "--plan", "design,implement").returncode
        )
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        lint = run_ccnavi(["--root", self.ws, "--cwd", self.tree, "--lint"], env=environment)
        self.assertIn("error 0 件", lint.stdout + lint.stderr, lint.stdout + lint.stderr)


if __name__ == "__main__":
    unittest.main()
