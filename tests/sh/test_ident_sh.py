"""sh の識別子の検査（`ccnavi-common.sh` の `ccnavi_is_ident`）。

日本語の識別子を通し、パスやシェルで意味を持つ表記（`/`・`..`・先頭の `-`・空白・制御文字・
記号）を止める。ロケール（C・C.UTF-8）とシェル（sh・bash）を変えても答えが同じこと、
前の形（`i0055-01`）も通ることを見る。字の種類（全角記号・NFD）は実行ファイルが止めるので、
ここでは ASCII の外のバイトは通す。

表は `tests/fixtures/ident-cases.json`。実行ファイルの答えは go test
（go/internal/tickets/model/ids/ident_sh_test.go）が同じ表で見る。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest

from tests import ROOT

TABLE = os.path.join(ROOT, "tests", "fixtures", "ident-cases.json")
COMMON = os.path.join(ROOT, ".ccnavi", "scripts", "ccnavi-common.sh")
SHELLS = [s for s in (shutil.which("sh"), shutil.which("bash")) if s]

with open(TABLE, encoding="utf-8") as _f:
    _CASES = json.load(_f)
GOOD = _CASES["ident_good"]
BAD = _CASES["ident_bad"]
# sh は通し、実行ファイルが止める（字の種類。2 段目の守り）。
SH_ONLY = _CASES["sh_only"]
# 親のブランチ名（`ccnavi_is_branch`）。識別子の字に段の区切りの `/` を足したもの。
BRANCH_GOOD = _CASES["branch_good"]
BRANCH_BAD = _CASES["branch_bad"]


def is_ident(shell, value, locale):
    # 値は引数でなく環境変数で渡す。Windows の引数は、改行や `'` をそのまま運べない。
    env = {**os.environ, "LC_ALL": locale, "CCNAVI_PROBE": value}
    done = subprocess.run(
        [shell, "-c", '. "$0"; ccnavi_is_ident "$CCNAVI_PROBE"', COMMON],
        env=env,
        capture_output=True,
    )
    return done.returncode == 0


@unittest.skipUnless(SHELLS, "sh が無い")
class IsIdentTest(unittest.TestCase):
    def test_the_answers(self):
        for shell in SHELLS:
            for locale in ("C", "C.UTF-8"):
                for value in GOOD:
                    with self.subTest(shell=shell, locale=locale, value=value):
                        self.assertTrue(is_ident(shell, value, locale))
                for value in BAD:
                    with self.subTest(shell=shell, locale=locale, value=value):
                        self.assertFalse(is_ident(shell, value, locale))

    def test_what_sh_lets_through_the_executable_checks(self):
        """sh は ASCII の外のバイトを通す。字の種類は実行ファイルが止める（go test が見る）。"""
        for value in SH_ONLY:
            with self.subTest(value=value):
                self.assertTrue(is_ident(SHELLS[0], value, "C"))


def is_branch(shell, value, locale):
    # 値は引数でなく環境変数で渡す。Windows の引数は、改行や `'` をそのまま運べない。
    env = {**os.environ, "LC_ALL": locale, "CCNAVI_PROBE": value}
    done = subprocess.run(
        [shell, "-c", '. "$0"; ccnavi_is_branch "$CCNAVI_PROBE"', COMMON],
        env=env,
        capture_output=True,
    )
    return done.returncode == 0


@unittest.skipUnless(SHELLS, "sh が無い")
class IsBranchTest(unittest.TestCase):
    def test_the_answers(self):
        for shell in SHELLS:
            for locale in ("C", "C.UTF-8"):
                for value in BRANCH_GOOD:
                    with self.subTest(shell=shell, locale=locale, value=value):
                        self.assertTrue(is_branch(shell, value, locale))
                for value in BRANCH_BAD:
                    with self.subTest(shell=shell, locale=locale, value=value):
                        self.assertFalse(is_branch(shell, value, locale))


if __name__ == "__main__":
    unittest.main()
