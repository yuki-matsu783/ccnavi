"""`scratchpad/` の容量の知らせ（tickets/notice/scratchpad）のテスト。"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from ccnavi.tickets.notice import scratchpad
from tests.inproc import run_ccnavi

MB = 1024 * 1024


def _put(root: str, rel: str, size: int) -> str:
    path = os.path.join(root, "scratchpad", rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)
    return path


class SizeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name

    def test_sum_includes_nested_files(self):
        _put(self.root, "a.bin", 100)
        _put(self.root, "sub/b.bin", 200)
        self.assertEqual(scratchpad.scratch_bytes(self.root), 300)

    def test_missing_dir_is_zero(self):
        self.assertEqual(scratchpad.scratch_bytes(self.root), 0)

    def test_symlinks_not_followed(self):
        target = _put(self.root, "real.bin", 100)
        try:
            os.symlink(target, os.path.join(self.root, "scratchpad", "link.bin"))
            os.symlink(self.root, os.path.join(self.root, "scratchpad", "dirlink"))
        except (OSError, NotImplementedError):
            self.skipTest("symlink 不可")
        self.assertEqual(scratchpad.scratch_bytes(self.root), 100)


    def test_scan_stops_at_the_limit(self):
        _put(self.root, "a.bin", 100)
        self.assertEqual(scratchpad.scratch_bytes(self.root, 0.0), 0)


class WarnMbTest(unittest.TestCase):
    def _with(self, value):
        env = {} if value is None else {scratchpad.WARN_MB_ENV: value}
        with mock.patch.dict(os.environ, env, clear=False):
            if value is None:
                os.environ.pop(scratchpad.WARN_MB_ENV, None)
            return scratchpad.warn_mb()

    def test_default_cases(self):
        for value in (None, "", "abc", "-1", "inf", "nan"):
            self.assertEqual(self._with(value), scratchpad.DEFAULT_WARN_MB, value)

    def test_values(self):
        self.assertEqual(self._with("0"), 0)
        self.assertEqual(self._with("2.5"), 2.5)


class NoticeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name

    def _text(self, limit: str) -> str:
        with mock.patch.dict(os.environ, {scratchpad.WARN_MB_ENV: limit}):
            return scratchpad.session_text(self.root)

    def test_over_limit_notifies(self):
        _put(self.root, "big.bin", 2 * MB)
        text = self._text("1")
        self.assertIn("scratchpad", text)
        self.assertIn("2 MB", text)

    def test_at_or_below_limit_silent(self):
        _put(self.root, "big.bin", 1 * MB)
        self.assertEqual(self._text("1"), "")

    def test_zero_disables(self):
        _put(self.root, "big.bin", 2 * MB)
        self.assertEqual(self._text("0"), "")

    def test_failure_is_swallowed(self):
        with mock.patch.object(scratchpad, "scratch_bytes", side_effect=RuntimeError):
            self.assertEqual(scratchpad.session_text(self.root), "")


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
            **extra,
        }
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        env.pop("CLAUDE_PROJECT_DIR", None)
        env.update(
            CCNAVI_LEARNING="disable",
            CCNAVI_ENTRY_PATH=".ccnavi/scripts/ccnavi-launcher.sh",
            **{scratchpad.WARN_MB_ENV: limit},
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

    def test_subagent_gets_no_notice(self):
        _put(self.root, "big.bin", 2 * MB)
        self.assertNotIn("しきい値", self._context("1", agent_id="a1"))


if __name__ == "__main__":
    unittest.main()
