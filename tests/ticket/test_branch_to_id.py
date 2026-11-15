"""issue から新しい親のブランチ名（= 識別子）を作るサブコマンド `ccnavi worktree issue-id`。

実行ファイル（go の ids）・sh（`ccnavi_branch_to_id`）・TS（拡張の `branchToId`）は、同じ表
（`tests/fixtures/branch-to-id.json`）で変換と採用の可否の一致を確かめる（Go は ids の go test）。
"""

from __future__ import annotations

import json
import os
import unittest

from tests import ROOT
from tests.inproc import run_ccnavi

TABLE = os.path.join(ROOT, "tests", "fixtures", "branch-to-id.json")


def load():
    with open(TABLE, encoding="utf-8") as f:
        return json.load(f)


class BranchToIdTest(unittest.TestCase):
    def ask(self, *words):
        root = os.path.join(ROOT, "tests", "fixtures")
        done = run_ccnavi(["--root", root, *words])
        return done.returncode, done.stdout.splitlines(), done.stderr

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
