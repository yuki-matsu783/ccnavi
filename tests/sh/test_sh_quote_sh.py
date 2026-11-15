"""sh の `ccnavi_sh_quote`（`ccnavi-common.sh`）が、実行ファイルの `shell.SingleQuote` と
同じ形で囲むこと。

表は `tests/fixtures/sh-quote-cases.json`。実行ファイルの答えは go test
（go/internal/infra/shell/quote_sh_test.go）が同じ表で見る。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest

from tests import ROOT

TABLE = os.path.join(ROOT, "tests", "fixtures", "sh-quote-cases.json")
COMMON = os.path.join(ROOT, ".ccnavi", "scripts", "ccnavi-common.sh")
SHELLS = [s for s in (shutil.which("sh"), shutil.which("bash")) if s]

with open(TABLE, encoding="utf-8") as _f:
    CASES = json.load(_f)["cases"]


def quote(shell, value):
    # 値は引数でなく環境変数で渡す。Windows の引数は、改行や `'` をそのまま運べない。
    done = subprocess.run(
        [shell, "-c", '. "$0"; ccnavi_sh_quote "$CCNAVI_PROBE"', COMMON],
        env={**os.environ, "CCNAVI_PROBE": value},
        capture_output=True,
        check=True,
    )
    out = done.stdout.decode("utf-8")
    return out[:-1] if out.endswith("\n") else out


@unittest.skipUnless(SHELLS, "sh が無い")
class ShQuoteTest(unittest.TestCase):
    def test_same_as_the_table(self):
        self.assertTrue(CASES)
        for shell in SHELLS:
            for case in CASES:
                with self.subTest(shell=shell, value=case["in"]):
                    self.assertEqual(quote(shell, case["in"]), case["out"])


if __name__ == "__main__":
    unittest.main()
