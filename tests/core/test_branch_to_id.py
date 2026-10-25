"""親のブランチ名 -> 親の識別子の変換（`ticket_ids.branch_to_id`）。

sh（`ccnavi_branch_to_id`）と TS（拡張の `branchToId`）は、同じ表
（`tests/fixtures/branch-to-id.json`）で一致を確かめる。
ここでは Python の側と、`ccnavi worktree id <ブランチ>`（sh が採用の可否を聞くサブコマンド）を見る。
"""

from __future__ import annotations

import io
import json
import os
import unicodedata
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

    def ask(self, *words):
        root = os.path.join(ROOT, "tests", "fixtures")
        out, err = io.StringIO(), io.StringIO()
        code = cli.run(io.StringIO(), out, err, ["--root", root, *words])
        return code, out.getvalue().splitlines(), err.getvalue()

    def test_exe_answers_whether_a_direct_or_child_worktree_can_use_the_name(self):
        """直接作業・子のワークツリーは 64 文字までで、末尾の -2 桁は断らない（`direct` の行）。

        字の種類・NFC は親と同じに断る（sh はマルチバイトの字を見分けないので、ここに聞く）。
        """
        nfd = unicodedata.normalize("NFD", "feature-が")
        for name, direct_ok, child in (
            ("i0001-01-01", True, True),
            ("topic-" + "a" * 58, True, False),
            ("topic-" + "a" * 59, False, False),
            ("feature-１２３", False, False),
            ("feature/１２", False, False),
            (nfd, False, False),
        ):
            with self.subTest(name=name):
                code, lines, err = self.ask("worktree", "id", name)
                self.assertEqual(code, 0, err)
                self.assertEqual(any(x.startswith("direct ") for x in lines), not direct_ok, lines)
                self.assertEqual("child yes" in lines, child, lines)

    def test_exe_makes_the_branch_of_an_issue_as_chrome_does(self):
        for args, title, ident in (
            (("7", "feature"), "Update 10 20", "feature-7-update"),
            (("7", "feature"), "Fix bug 12", "feature-7-fix-bug"),
            (("7", "fix", "web"), "ログイン画面", "fix-7-web-ログイン画面"),
        ):
            with self.subTest(title=title):
                code, lines, err = self.ask("worktree", "issue-id", *args, "--title", title)
                self.assertEqual(code, 0, err)
                self.assertEqual(lines, [f"id {ident}"])
                project = args[2] if len(args) == 3 else ""
                self.assertEqual(
                    ticket_ids.issue_identifier(int(args[0]), title, project, args[1]), ident
                )
        # 題が長くて slug を短くしたときは、`long yes` を添える（start.sh が短い --slug を求める）
        title = "テキスト整形CLI（main.sh）を README の仕様どおりに実装する"
        code, lines, err = self.ask("worktree", "issue-id", "41", "feature", "--title", title)
        self.assertEqual(code, 0, err)
        self.assertEqual(lines, ["id feature-41-テキスト整形cli-main-sh-を-readme", "long yes"])
        code, lines, err = self.ask("worktree", "issue-id", "7", "Bad-Prefix", "--title", "x")
        self.assertNotEqual(code, 0)
        self.assertEqual(lines, [])


if __name__ == "__main__":
    unittest.main()
