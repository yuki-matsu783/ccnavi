"""sh の `ccnavi_same_name`（`ccnavi-common.sh`）が、大文字小文字を区別せず名前を比べること。

畳むのは ASCII の英字だけ（`ccnavi_path_pick` と同じ）。実行ファイルの畳み方（pathcase.Fold）との
一致表は置かない。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import unittest

from tests import ROOT

COMMON = os.path.join(ROOT, ".ccnavi", "scripts", "ccnavi-common.sh")
SHELLS = [s for s in (shutil.which("sh"), shutil.which("bash")) if s]

# (名前 A, 名前 B, 同じなら 0 / 違えば 1)
CASES = [
    ("abc", "abc", 0),
    ("Foo/Bar", "foo/bar", 0),
    ("FOO/BAR", "foo/bar", 0),
    ("foo/bar", "foo/baz", 1),
    ("foo", "", 1),
    ("", "foo", 1),
    ("it's", "IT'S", 0),
    ("a'b", "a'c", 1),
    ("a\\b", "A\\B", 0),
    ("a\\b", "a/b", 1),
]


def same(shell, a, b):
    # 値は引数でなく環境変数で渡す。Windows の引数は、改行や `'` をそのまま運べない。
    done = subprocess.run(
        [shell, "-c", '. "$0"; ccnavi_same_name "$CCNAVI_A" "$CCNAVI_B"', COMMON],
        env={**os.environ, "CCNAVI_A": a, "CCNAVI_B": b},
        capture_output=True,
    )
    return done.returncode


@unittest.skipUnless(SHELLS, "sh が無い")
class SameNameTest(unittest.TestCase):
    def test_table(self):
        for shell in SHELLS:
            for a, b, want in CASES:
                with self.subTest(shell=shell, a=a, b=b):
                    self.assertEqual(same(shell, a, b), want)


if __name__ == "__main__":
    unittest.main()
