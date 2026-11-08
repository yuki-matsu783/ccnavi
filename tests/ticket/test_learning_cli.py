"""学びの蓄積の CLI（`ticket learn`）を確かめる。子を閉じる検査は go の learning のテストが見る。"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from tests.inproc import run_ccnavi


class LearningCliTest(unittest.TestCase):
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

    def learn(self, *words: str, enable: bool = True):
        env = dict(self.env)
        if not enable:
            env["CCNAVI_LEARNING"] = "disable"
        return run_ccnavi(
            ["--root", self.ws, "ticket", "learn", *words], input="", env=env, cwd=self.ws
        )

    def direct_worktree(self) -> str:
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
        return wt

    def test_note_in_a_direct_worktree_goes_to_the_inbox(self) -> None:
        wt = self.direct_worktree()
        result = run_ccnavi(
            [
                "--root",
                self.ws,
                "ticket",
                "learn",
                "--note",
                "テストの気づき\n2 行目",
                "--target",
                "skill",
            ],
            input="",
            env=dict(self.env),
            cwd=wt,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        inbox = os.path.join(wt, "docs", "wiki", "inbox")
        names = os.listdir(inbox)
        self.assertEqual(len(names), 1, names)
        name = names[0]
        self.assertTrue(name.startswith("direct-1-x-") and name.endswith(".md"), name)
        with open(os.path.join(inbox, name), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("type: learning-wiki", text)
        self.assertIn("- 種類: skill", text)
        self.assertIn("- 信号: 訂正", text)
        self.assertIn("テストの気づき", text)
        self.assertIn("  2 行目", text)

    def test_note_at_the_root_is_refused(self) -> None:
        result = self.learn("--note", "x")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ワークツリーを切", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "wip")))

    def test_disabled_and_invalid(self) -> None:
        result = self.learn("--note", "x", enable=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CCNAVI_LEARNING", result.stderr)

        wt = self.direct_worktree()
        result = run_ccnavi(
            ["--root", self.ws, "ticket", "learn", "--note", "x", "--target", "zzz"],
            input="",
            env=dict(self.env),
            cwd=wt,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--target", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(wt, "docs")))


if __name__ == "__main__":
    unittest.main()
