"""親のブランチ名 -> 親の識別子の変換（`ccnavi worktree id <ブランチ>`）。

実行ファイル（go の ids）・sh（`ccnavi_branch_to_id`）・TS（拡張の `branchToId`）は、同じ表
（`tests/fixtures/branch-to-id.json`）で一致を確かめる。
ここでは sh が採用の可否を聞くサブコマンド `ccnavi worktree id`・`issue-id` を見る。
"""

from __future__ import annotations

import json
import os
import unicodedata
import unittest

from tests import ROOT
from tests.inproc import run_ccnavi

TABLE = os.path.join(ROOT, "tests", "fixtures", "branch-to-id.json")


def load():
    with open(TABLE, encoding="utf-8") as f:
        return json.load(f)


class BranchToIdTest(unittest.TestCase):
    def test_exe_answers_id_and_problem(self):
        table = load()
        ids = {case["branch"]: case["id"] for case in table["convert"]}
        for case in table["adopt"]:
            with self.subTest(branch=case["branch"]):
                code, lines, err = self.ask("worktree", "id", case["branch"])
                self.assertEqual(code, 0, err)
                self.assertTrue(lines[0].startswith("id "), lines)
                if case["branch"] in ids:
                    self.assertEqual(lines[0], "id " + ids[case["branch"]])
                self.assertEqual(len(lines) == 1, case["ok"], lines)

    def ask(self, *words):
        root = os.path.join(ROOT, "tests", "fixtures")
        done = run_ccnavi(["--root", root, *words])
        return done.returncode, done.stdout.splitlines(), done.stderr

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
        # タイトルが長くて slug を短くしたときは、`long yes` を添える
        # （start.sh が短い --slug を求める）
        title = "テキスト整形CLI（main.sh）を README の仕様どおりに実装する"
        code, lines, err = self.ask("worktree", "issue-id", "41", "feature", "--title", title)
        self.assertEqual(code, 0, err)
        self.assertEqual(lines, ["id feature-41-テキスト整形cli-main-sh-を-readme", "long yes"])
        code, lines, err = self.ask("worktree", "issue-id", "7", "Bad-Prefix", "--title", "x")
        self.assertNotEqual(code, 0)
        self.assertEqual(lines, [])


if __name__ == "__main__":
    unittest.main()
