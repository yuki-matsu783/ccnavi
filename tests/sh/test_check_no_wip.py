"""scripts/ccnavi-check-no-wip.sh: 統合先へ入れる差分に wip/ が入っていれば止める。"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest

SH = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "ccnavi-check-no-wip.sh")


def git(cwd: str, *a: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, check=True, capture_output=True)


class CheckNoWip(unittest.TestCase):
    def run_check(self, files: list[str]) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as d:
            git(d, "init", "-q", "-b", "main")
            git(d, "commit", "-q", "--allow-empty", "-m", "base")
            git(d, "checkout", "-q", "-b", "topic")
            for f in files:
                p = os.path.join(d, f)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w") as fh:
                    fh.write("x\n")
            git(d, "add", "-A")
            git(d, "commit", "-q", "--allow-empty", "-m", "topic")
            return subprocess.run(["sh", os.path.abspath(SH), "main"], cwd=d, capture_output=True, text=True)

    def test_wip_in_diff_fails(self) -> None:
        r = self.run_check(["src/a.py", "wip/proposals/todo/x.md"])
        self.assertEqual(r.returncode, 1)
        self.assertIn("wip/proposals/todo/x.md", r.stderr)

    def test_no_wip_passes(self) -> None:
        self.assertEqual(self.run_check(["src/a.py"]).returncode, 0)

    def test_handoff_is_allowed(self) -> None:
        self.assertEqual(self.run_check(["wip/handoff/learnings/a.md"]).returncode, 0)


if __name__ == "__main__":
    unittest.main()
