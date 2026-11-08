"""`scratchpad/` の容量の知らせが、hook を通した SessionStart の文脈に出ること。

サブエージェントには出ない。

容量の数え方としきい値の読み方は `go/internal/tickets/notice` の go test が見る。
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest

from tests.inproc import run_ccnavi

MB = 1024 * 1024
WARN_MB_ENV = "CCNAVI_SCRATCHPAD_WARN_MB"


def _put(root: str, rel: str, size: int) -> str:
    path = os.path.join(root, "scratchpad", rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)
    return path


class SessionStartWiringTest(unittest.TestCase):
    """hook を通した SessionStart で、知らせが文脈に出る（サブエージェントには出ない）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.realpath(self._tmp.name)
        subprocess.run(["git", "init", "-q", self.root], check=True)

    def _context(self, limit: str, **extra) -> str:
        payload = {
            "hook_event_name": "SessionStart",
            "session_id": "s1",
            "cwd": self.root,
            "source": "startup",
            **extra,
        }
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        env.pop("CLAUDE_PROJECT_DIR", None)
        env.update(
            CCNAVI_LEARNING="disable",
            CCNAVI_ENTRY_PATH=".ccnavi/scripts/ccnavi-launcher.sh",
            **{WARN_MB_ENV: limit},
        )
        done = run_ccnavi(
            ["--root", self.root, "--mode", "enable"],
            input=json.dumps(payload),
            cwd=self.root,
            env=env,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        if not done.stdout.strip():
            return ""
        return json.loads(done.stdout)["hookSpecificOutput"].get("additionalContext", "")

    def test_notice_reaches_the_session_context(self):
        _put(self.root, "big.bin", 2 * MB)
        self.assertIn("しきい値", self._context("1"))

    def test_no_notice_below_limit(self):
        _put(self.root, "small.bin", 100)
        self.assertNotIn("しきい値", self._context("1"))

    def test_only_startup_notifies(self):
        _put(self.root, "big.bin", 2 * MB)
        for source in ("resume", "compact", "clear"):
            self.assertNotIn("しきい値", self._context("1", source=source), source)

    def test_subagent_gets_no_notice(self):
        _put(self.root, "big.bin", 2 * MB)
        self.assertNotIn("しきい値", self._context("1", agent_id="a1"))


if __name__ == "__main__":
    unittest.main()
