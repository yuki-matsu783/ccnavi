"""sh の `ccnavi_branch_to_id`（`ccnavi-common.sh`）が共通の表と一致すること。

表は `tests/fixtures/branch-to-id.json`。

Python（`ticket_ids.branch_to_id`）・TS（拡張の `branchToId`）も同じ表を読む。
同じ識別子になる別のブランチの見つけ方（`ccnavi_id_clash`。英字の大文字小文字は区別しない）もここで確かめる。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
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

    def run_sh(self, shell, script, *args):
        return subprocess.run(
            [shell, "-c", '. "$0"; ' + script, COMMON, *args],
            capture_output=True,
            env={**os.environ, "LC_ALL": "C"},
        )

    def test_id_clash_ignores_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            heads = os.path.join(tmp, "heads")
            with open(heads, "w", encoding="utf-8") as f:
                f.write("main\nFeature/12-X\nfeature/12-x\nfeature-12-xy\n")
            for shell in SHELLS:
                with self.subTest(shell=shell):
                    done = self.run_sh(shell, 'ccnavi_id_clash "$1" "$2"', "feature/12-x", heads)
                    self.assertEqual(done.returncode, 0)
                    self.assertEqual(done.stdout.decode().strip(), "Feature/12-X")
                    alone = self.run_sh(shell, 'ccnavi_id_clash "$1" "$2"', "feature/99-x", heads)
                    self.assertEqual(alone.returncode, 1)


if __name__ == "__main__":
    unittest.main()
