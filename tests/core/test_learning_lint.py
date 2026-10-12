"""CCNAVI_LEARNING の書き誤りを `ccnavi --lint` が warn で言うかを確かめる。"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from tests.inproc import run_ccnavi


class LearningLintTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ws = tempfile.mkdtemp(prefix="ccnavi-learn-lint-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        os.makedirs(os.path.join(self.ws, ".claude"))
        subprocess.run(
            ["git", "init", "--quiet", "-b", "main"], cwd=self.ws, check=True, capture_output=True
        )
        self.base_env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        self.base_env.pop("CLAUDE_PROJECT_DIR", None)

    def lint(self, value: str | None):
        env = dict(self.base_env)
        if value is not None:
            env["CCNAVI_LEARNING"] = value
        return run_ccnavi(["--root", self.ws, "--lint"], input="", env=env, cwd=self.ws)

    def test_invalid_value_is_warned(self) -> None:
        result = self.lint("on")
        self.assertIn("CCNAVI_LEARNING=on", result.stdout + result.stderr)

    def test_enable_is_not_warned(self) -> None:
        result = self.lint("enable")
        self.assertNotIn("CCNAVI_LEARNING", result.stdout + result.stderr)

    def test_unset_is_not_warned(self) -> None:
        result = self.lint(None)
        self.assertNotIn("CCNAVI_LEARNING", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
