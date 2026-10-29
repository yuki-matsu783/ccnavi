"""学びの蓄積の CLI（`ticket learn`）と、子を閉じる検査（record_missing）を確かめる。"""

from __future__ import annotations

import dataclasses
import io
import os
import shutil
import subprocess
import tempfile
import types
import unittest

from ccnavi.infra import settings
from ccnavi.tickets.learning import notes as learning
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

    def test_record_missing_needs_tracked_record(self) -> None:
        child = "feat-1-01-01"
        wt = os.path.join(self.ws, ".claude", "worktrees", child)
        os.makedirs(wt)
        subprocess.run(
            ["git", "init", "--quiet", "-b", "main"], cwd=wt, check=True, capture_output=True
        )
        conf, _ = settings.load(self.ws)
        conf = dataclasses.replace(conf, learning="enable")
        found = types.SimpleNamespace(is_child=True, is_consolidate=False, ticket=child, phase=None)

        err = io.StringIO()
        self.assertTrue(learning.record_missing(err, self.ws, conf, found))
        self.assertIn("learn", err.getvalue())

        rec = os.path.join(wt, learning.record_rel(child))
        os.makedirs(os.path.dirname(rec))
        with open(rec, "w", encoding="utf-8") as f:
            f.write(learning.scaffold_text(child, "feat-1"))
        # 追跡前なので、まだ閉じさせない。
        self.assertTrue(learning.record_missing(io.StringIO(), self.ws, conf, found))

        subprocess.run(
            ["git", "add", "-f", learning.record_rel(child)],
            cwd=wt,
            check=True,
            capture_output=True,
        )
        # 追加しただけで、コミット前なので、まだ閉じさせない。
        self.assertTrue(learning.record_missing(io.StringIO(), self.ws, conf, found))
        subprocess.run(
            ["git", "-c", "user.email=a@b.c", "-c", "user.name=n", "commit", "-q", "-m", "x"],
            cwd=wt,
            check=True,
            capture_output=True,
        )
        self.assertFalse(learning.record_missing(io.StringIO(), self.ws, conf, found))

        disabled = dataclasses.replace(conf, learning="disable")
        found2 = types.SimpleNamespace(is_child=True, is_consolidate=False, ticket="feat-1-01-02")
        self.assertFalse(learning.record_missing(io.StringIO(), self.ws, disabled, found2))


if __name__ == "__main__":
    unittest.main()
