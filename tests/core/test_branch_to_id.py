"""親のブランチ名 -> 親の識別子の変換（`ticket_ids.branch_to_id`）。

sh（`ccnavi_branch_to_id`）と TS（拡張の `branchToId`）は、同じ表
（`tests/fixtures/branch-to-id.json`）で一致を確かめる。
ここでは Python の側と、`ccnavi worktree id <ブランチ>`（sh が採用の可否を聞く副命令）を見る。
"""

from __future__ import annotations

import io
import json
import os
import unittest

from ccnavi.entry import cli
from ccnavi.tickets import ticket_ids
from tests import ROOT

TABLE = os.path.join(ROOT, "tests", "fixtures", "branch-to-id.json")


def load():
    with open(TABLE, encoding="utf-8") as f:
        return json.load(f)


class BranchToIdTest(unittest.TestCase):
    def test_table(self):
        for case in load()["convert"]:
            with self.subTest(branch=case["branch"]):
                self.assertEqual(ticket_ids.branch_to_id(case["branch"]), case["id"])

    def test_adoption(self):
        for case in load()["adopt"]:
            with self.subTest(branch=case["branch"]):
                problem = ticket_ids.adoption_id_problem(case["branch"])
                self.assertEqual(problem == "", case["ok"], problem)

    def test_exe_answers_id_and_problem(self):
        root = os.path.join(ROOT, "tests", "fixtures")
        for case in load()["adopt"]:
            with self.subTest(branch=case["branch"]):
                out, err = io.StringIO(), io.StringIO()
                code = cli.run(
                    io.StringIO(), out, err, ["--root", root, "worktree", "id", case["branch"]]
                )
                self.assertEqual(code, 0, err.getvalue())
                lines = out.getvalue().splitlines()
                self.assertEqual(lines[0], "id " + ticket_ids.branch_to_id(case["branch"]))
                self.assertEqual(len(lines) == 1, case["ok"], out.getvalue())


if __name__ == "__main__":
    unittest.main()
