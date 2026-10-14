"""取り込み記録の書き方（`ccnavi_record_write` / `ccnavi_record_put`）。

`mr`（親を載せる MR の番号。ccnavi-start.sh が保存する）は、
sync・push などが記録を書き直しても残ること。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from tests import ROOT

COMMON = os.path.join(ROOT, ".ccnavi", "scripts", "ccnavi-common.sh")
SHELLS = [s for s in (shutil.which("sh"), shutil.which("bash")) if s]


def sh(shell, script, *args):
    return subprocess.run(
        [shell, "-c", f'. "$0"; {script}', COMMON, *args], capture_output=True, text=True
    )


@unittest.skipIf(not SHELLS, "sh が無い")
class RecordShTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.file = os.path.join(self._tmp.name, "sync", "self", "families", "feature-1-x")

    def read(self):
        with open(self.file, encoding="utf-8") as f:
            return f.read().splitlines()

    def test_write_keeps_mr_unless_given(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                if os.path.exists(self.file):
                    os.remove(self.file)
                done = sh(
                    shell,
                    'ccnavi_record_write "$1" state present sha aaa mr 12 && '
                    'ccnavi_record_write "$1" state gone sha bbb',
                    self.file,
                )
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(self.read(), ["state gone", "sha bbb", "mr 12"])
                done = sh(shell, 'ccnavi_record_write "$1" state present mr 13', self.file)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(self.read(), ["state present", "mr 13"])

    def test_put_replaces_or_appends_one_key(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                if os.path.exists(self.file):
                    os.remove(self.file)
                missing = sh(shell, 'ccnavi_record_put "$1" mr 5', self.file)
                self.assertNotEqual(missing.returncode, 0)
                sh(shell, 'ccnavi_record_write "$1" state present sha aaa', self.file)
                self.assertEqual(sh(shell, 'ccnavi_record_put "$1" mr 5', self.file).returncode, 0)
                self.assertEqual(self.read(), ["state present", "sha aaa", "mr 5"])
                self.assertEqual(sh(shell, 'ccnavi_record_put "$1" mr 6', self.file).returncode, 0)
                self.assertEqual(self.read(), ["state present", "sha aaa", "mr 6"])


if __name__ == "__main__":
    unittest.main()
