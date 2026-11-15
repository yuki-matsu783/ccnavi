"""sh の `ccnavi_branch_to_id`（`ccnavi-common.sh`）が共通の表と一致すること。

表は `tests/fixtures/branch-to-id.json`。

実行ファイル（go/internal/tickets/model/ids の BranchToID）・TS（拡張の `branchToId`）も同じ表を読む。
ワークツリーの HEAD の照合は実行ファイル（standing.HeadBranch）が持ち、go test が見る。
同じ識別子になる別のブランチの見つけ方（`ccnavi_id_clash`）は、
実行ファイル（ids.IDClash）が持ち、go test が見る。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest

from tests import ROOT

COMMON = os.path.join(ROOT, ".ccnavi", "scripts", "ccnavi-common.sh")
TABLE = os.path.join(ROOT, "tests", "fixtures", "branch-to-id.json")
SHELLS = [s for s in (shutil.which("sh"), shutil.which("bash")) if s]


def convert(shell, branch):
    done = subprocess.run(
        [shell, "-c", '. "$0"; ccnavi_branch_to_id "$1"', COMMON, branch],
        capture_output=True,
        env={**os.environ, "LC_ALL": "C"},
    )
    return done.stdout.decode("utf-8").rstrip("\n")


@unittest.skipIf(not SHELLS, "sh が無い")
class BranchToIdShTest(unittest.TestCase):
    def test_table(self):
        with open(TABLE, encoding="utf-8") as f:
            cases = json.load(f)["convert"]
        for shell in SHELLS:
            for case in cases:
                with self.subTest(shell=shell, branch=case["branch"]):
                    self.assertEqual(convert(shell, case["branch"]), case["id"])


if __name__ == "__main__":
    unittest.main()
