"""学びの蓄積の CLI（`ticket learn`）を確かめる。

記録の書き方の細部は go の learning のテストが見る。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from tests import requires_symlink
from tests.inproc import run_ccnavi


class LearningBase(unittest.TestCase):
    def setUp(self) -> None:
        self.ws = tempfile.mkdtemp(prefix="ccnavi-learn-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        os.makedirs(os.path.join(self.ws, ".claude"))
        subprocess.run(
            ["git", "init", "--quiet", "-b", "main"], cwd=self.ws, check=True, capture_output=True
        )
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        self.env.pop("CLAUDE_PROJECT_DIR", None)
        self.env["CCNAVI_LEARNING"] = "enable"

    def learn(self, *words: str, cwd: str | None = None):
        return run_ccnavi(
            ["--root", self.ws, "ticket", "learn", *words],
            input="",
            env=self.env,
            cwd=cwd or self.ws,
        )

    def direct_worktree(self, name: str = "direct-1-x") -> str:
        """チケットに結び付かない直接作業のワークツリーを切る。"""
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=a@b.c",
                "-c",
                "user.name=n",
                "commit",
                "--allow-empty",
                "-q",
                "-m",
                "init",
            ],
            cwd=self.ws,
            check=True,
            capture_output=True,
        )
        wt = os.path.join(self.ws, ".claude", "worktrees", name)
        subprocess.run(
            ["git", "worktree", "add", wt, "-b", name],
            cwd=self.ws,
            check=True,
            capture_output=True,
        )
        return wt

    def read(self, path: str) -> str:
        with open(path, encoding="utf-8", newline="") as f:
            return f.read()


class LearningMoreTest(LearningBase):
    def test_inbox_names_do_not_collide(self) -> None:
        wt = self.direct_worktree()
        for marker in ("気づきA", "気づきB", "気づきC"):
            result = self.learn("--note", marker, cwd=wt)
            self.assertEqual(result.returncode, 0, result.stderr)
        inbox = os.path.join(wt, "docs", "wiki", "inbox")
        names = sorted(os.listdir(inbox))
        self.assertEqual(len(names), 3, names)
        found = []
        for name in names:
            text = self.read(os.path.join(inbox, name))
            hits = [m for m in ("気づきA", "気づきB", "気づきC") if m in text]
            self.assertEqual(len(hits), 1, (name, hits))
            found.append(hits[0])
        self.assertEqual(sorted(found), ["気づきA", "気づきB", "気づきC"])

    def test_direct_worktree_inbox_goes_to_docs_wiki(self) -> None:
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=a@b.c",
                "-c",
                "user.name=n",
                "commit",
                "--allow-empty",
                "-q",
                "-m",
                "init",
            ],
            cwd=self.ws,
            check=True,
            capture_output=True,
        )
        wt = os.path.join(self.ws, ".claude", "worktrees", "direct-1-x")
        subprocess.run(
            ["git", "worktree", "add", wt, "-b", "direct-1-x"],
            cwd=self.ws,
            check=True,
            capture_output=True,
        )
        result = self.learn("--note", "直接作業の気づき", cwd=wt)
        self.assertEqual(result.returncode, 0, result.stderr)
        inbox = os.path.join(wt, "docs", "wiki", "inbox")
        names = os.listdir(inbox)
        self.assertEqual(len(names), 1, names)
        self.assertTrue(names[0].startswith("direct-1-x-"), names)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "wip", "handoff", "inbox")))

    def test_signal_and_target_rejected_via_cli(self) -> None:
        wt = self.direct_worktree()
        result = self.learn("--note", "x", "--signal", "zzz", cwd=wt)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--signal", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(wt, "docs")))

        for blank in ("", "   "):
            result = self.learn("--note", blank, cwd=wt)
            self.assertNotEqual(result.returncode, 0, repr(blank))
        self.assertFalse(os.path.exists(os.path.join(wt, "docs")))

    def test_secret_is_redacted_in_note(self) -> None:
        secret = "ghp_" + "0123456789abcdefghijklmnopqrstuvwxyz"
        wt = self.direct_worktree()
        result = self.learn("--note", f"接続に使った {secret} を忘れない", cwd=wt)
        self.assertEqual(result.returncode, 0, result.stderr)
        inbox = os.path.join(wt, "docs", "wiki", "inbox")
        (name,) = os.listdir(inbox)
        text = self.read(os.path.join(inbox, name))
        self.assertNotIn(secret, text)

    @requires_symlink
    def test_symlink_destination_is_refused(self) -> None:
        outside = tempfile.mkdtemp(prefix="ccnavi-learn-outside-")
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        os.makedirs(os.path.join(self.ws, "wip"))
        os.symlink(outside, os.path.join(self.ws, "wip", "handoff"))
        result = self.learn("--note", "x")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(os.listdir(outside), [])

    def test_workspace_and_project_roots_refuse_and_point_to_a_worktree(self) -> None:
        result = self.learn("--note", "n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ワークツリーを切", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "wip")))
        result = self.learn()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "wip")))
        proj = os.path.join(self.ws, "projects", "foo")
        os.makedirs(proj)
        subprocess.run(
            ["git", "init", "--quiet", "-b", "main"], cwd=proj, check=True, capture_output=True
        )
        result = self.learn("--note", "n", cwd=proj)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ワークツリーを切", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(proj, "wip")))
        self.assertFalse(os.path.exists(os.path.join(self.ws, "wip")))


if __name__ == "__main__":
    unittest.main()
