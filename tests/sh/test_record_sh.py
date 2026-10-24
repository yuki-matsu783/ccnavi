"""1 行 1 項目のファイルの書き方（`ccnavi_record_write` / `ccnavi_record_get`）。

`integrate` のマーカー（`ccnavi-git.sh integrate`）が使う。親子ごとの取り込み状態は
持たなくなったので、`mr` を前の値から引き継ぐ動きも、1 項目だけを書き足す
`ccnavi_record_put` も無い。
渡したキーだけを書き、値の改行は空白に置き換え、書き直しは一時ファイルを経る。
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
        self.file = os.path.join(self._tmp.name, "marker", "feature-1-x", "marker")

    def read(self):
        with open(self.file, encoding="utf-8") as f:
            return f.read().splitlines()

    def test_write_keeps_only_the_given_keys(self):
        """`mr` も、渡さなければ前の値を引き継がない。"""
        for shell in SHELLS:
            with self.subTest(shell=shell):
                if os.path.exists(self.file):
                    os.remove(self.file)
                done = sh(
                    shell,
                    'ccnavi_record_write "$1" state staged sha aaa mr 12 && '
                    'ccnavi_record_write "$1" state conflict sha bbb',
                    self.file,
                )
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(self.read(), ["state conflict", "sha bbb"])
                done = sh(shell, 'ccnavi_record_write "$1" state staged mr 13', self.file)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(self.read(), ["state staged", "mr 13"])

    def test_get_reads_one_key_or_nothing(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                if os.path.exists(self.file):
                    os.remove(self.file)
                missing = sh(shell, 'printf "[%s]" "$(ccnavi_record_get "$1" state)"', self.file)
                self.assertEqual(missing.stdout, "[]")
                sh(shell, 'ccnavi_record_write "$1" state staged branch feature/x', self.file)
                got = sh(shell, 'ccnavi_record_get "$1" branch', self.file)
                self.assertEqual(got.stdout.strip(), "feature/x")
                none = sh(shell, 'printf "[%s]" "$(ccnavi_record_get "$1" nokey)"', self.file)
                self.assertEqual(none.stdout, "[]")

    def test_newlines_in_a_value_become_spaces(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                done = sh(
                    shell,
                    'ccnavi_record_write "$1" state staged reason "$(printf "a\\nb")"',
                    self.file,
                )
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(self.read(), ["state staged", "reason a b"])

    def test_the_family_record_helpers_are_gone(self):
        """親子ごとの取り込み状態を扱う関数は、もう無い。"""
        for shell in SHELLS:
            with self.subTest(shell=shell):
                for name in ("ccnavi_record_put", "ccnavi_family_record"):
                    done = sh(shell, f"command -v {name}")
                    self.assertNotEqual(done.returncode, 0, name)


if __name__ == "__main__":
    unittest.main()
