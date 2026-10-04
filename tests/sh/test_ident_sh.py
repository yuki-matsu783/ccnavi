"""sh の識別子の検査（`ccnavi-common.sh` の `ccnavi_is_ident`）。

日本語の識別子を通し、パスやシェルで意味を持つ表記（`/`・`..`・先頭の `-`・空白・制御文字・
記号）を止める。ロケール（C・C.UTF-8）とシェル（sh・bash）を変えても答えが同じこと、
前の形（`i0055-01`）も通ることを見る。字の種類（全角記号・NFD）は実行ファイルが止めるので、
ここでは ASCII の外のバイトは通す（`ticket_ids.id_problem` と並べて見る）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import unicodedata
import unittest

from ccnavi.tickets import ticket_ids
from tests import ROOT

COMMON = os.path.join(ROOT, ".ccnavi", "scripts", "ccnavi-common.sh")
SHELLS = [s for s in (shutil.which("sh"), shutil.which("bash")) if s]

GOOD = [
    "feature-64-統合先の解決",
    "hotfix-7-ログイン画面ー",
    "i0055",
    "i0055-01",
    "a",
    "fix-1-a.b_c",
]
BAD = [
    "",
    "-x",
    ".x",
    "a/b",
    "a..b",
    "a b",
    "a\tb",
    "a\nb",
    "ab\n",
    "a$b",
    "a;b",
    "a`b",
    "a'b",
    'a"b',
    "a\\b",
    "a*b",
    "a?b",
    "a|b",
    "a&b",
    "a(b",
    "a:b",
    "a~b",
    "a\x7fb",
    "a\x01b",
    "統合",
]


def is_ident(shell, value, locale):
    env = {**os.environ, "LC_ALL": locale}
    done = subprocess.run(
        [shell, "-c", '. "$0"; ccnavi_is_ident "$1"', COMMON, value],
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
        """sh は ASCII の外のバイトを通す。字の種類は実行ファイルが止める（2 段目の守り）。"""
        for value in ("feature-1-ＡＢ", unicodedata.normalize("NFD", "feature-1-が")):
            with self.subTest(value=value):
                self.assertTrue(is_ident(SHELLS[0], value, "C"))
                self.assertFalse(ticket_ids.is_valid_id(value))
        for value in GOOD:
            with self.subTest(value=value):
                self.assertTrue(ticket_ids.is_valid_id(value))


# 親のブランチ名（`ccnavi_is_branch`）。
# 識別子の字に段の区切りの `/` を足したもの。
BRANCH_GOOD = ["feature/123-login", "hotfix/45", "user/x_y.z", "feature-1-x", "a/日本語"]
BRANCH_BAD = [
    "",
    "-x",
    ".x",
    "/x",
    "x/",
    "x.",
    "a..b",
    "a//b",
    "a/.b",
    "x.lock",
    "x.lock/y",
    "a b",
    "a\nb",
    "ab\n",
    "a$b",
    "a;b",
    "a\\b",
    "a:b",
    "a~b",
    "a@{1}",
    "統合/x",
    "HEAD",
    "origin/main",
    "origin/HEAD",
    "Origin/x",
    "refs/heads/main",
    "refs/remotes/origin/main",
    "heads/main",
    "remotes/x",
    "tags/v1",
    "upstream/x",
    "FETCH_HEAD",
    "main",
    "main/x",
    "Master/x",
    "develop/x",
    "release",
    "release/1",
    "Release-2",
    "x/HEAD",
    "x/ORIG_HEAD/y",
]


def is_branch(shell, value, locale):
    env = {**os.environ, "LC_ALL": locale}
    done = subprocess.run(
        [shell, "-c", '. "$0"; ccnavi_is_branch "$1"', COMMON, value],
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

    def test_the_executable_agrees(self):
        for value in BRANCH_GOOD:
            with self.subTest(value=value):
                self.assertEqual(ticket_ids.branch_problem(value), "")
        for value in BRANCH_BAD:
            with self.subTest(value=value):
                self.assertNotEqual(ticket_ids.branch_problem(value), "")


if __name__ == "__main__":
    unittest.main()
