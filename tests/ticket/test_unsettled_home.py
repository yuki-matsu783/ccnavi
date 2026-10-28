"""親のワークツリーが決まらないとき、読む側はその置き場のマーカーを読まない。

親のフォルダ（`.claude/worktrees/<親>`）が実在しても、HEAD が別のブランチ・途中の操作
（rebase など）があるなどで決まらなければ、そこにあるマーカー（レビュー済み・早めに閉じた）は
親のものとは限らない。読む側は「何も無い」と読み、書く側は止まる。

道具は依頼までの道の促しの受入テスト（test_stop_request_nudge.RequestNudgeTest）のものを借りる。
"""

from __future__ import annotations

import io
import json
import os
import unittest

from ccnavi.infra import settings
from ccnavi.tickets import ops_close, phase
from ccnavi.tickets.approval import checks as approval_checks
from ccnavi.tickets.approval import store as approval
from tests.ticket.test_stop_request_nudge import RequestNudgeTest
from tests.ticket.test_ticket import git, write


def load_tests(loader, tests, pattern):
    """このモジュールで書いたテストだけを走らせる（借りたテストは走らせない）。"""
    suite = unittest.TestSuite()
    for name in sorted(vars(UnsettledHomeTest)):
        if name.startswith("test_"):
            suite.addTest(UnsettledHomeTest(name))
    return suite


class UnsettledHomeTest(RequestNudgeTest):
    def plant_marks(self):
        """親のフォルダに、レビュー済みと早めに閉じたのマーカーを置く。"""
        marks = os.path.join(self.approved, "phases", "i0001")
        write(os.path.join(marks, "close-early.json"), json.dumps({"at": "2026-01-01"}))
        write(os.path.join(marks, "1.reviewed"), json.dumps({"at": "2026-01-01"}))

    def assert_marks_not_read(self, conf):
        self.assertEqual([p.marks for p in phase.phases_of(self.root, conf, "i0001")], [{}])
        problems = ops_close.close_problems(self.root, conf, "i0001")
        self.assertTrue(problems)
        self.assertIn("フェーズ 1", problems[0])
        where = approval.home_dir_read(conf, self.root, "i0001", "")
        self.assertFalse(os.path.exists(where), where)

    def test_marks_on_another_branch_of_the_parent_folder_are_not_read(self):
        self.family(plan=["work-mr"])
        self.close_children(work=False)
        conf, _ = settings.load(self.root)
        # 親のフォルダで別のブランチを checkout し、そこにマーカーを置く。
        git(self.parent_tree, "checkout", "--quiet", "-b", "other")
        self.plant_marks()
        self.assert_marks_not_read(conf)

    def busy(self):
        """親のワークツリーを rebase の途中（HEAD は切り離される）にする。"""
        gitdir = git(self.parent_tree, "rev-parse", "--git-dir").strip()
        if not os.path.isabs(gitdir):
            gitdir = os.path.join(self.parent_tree, gitdir)
        head = git(self.parent_tree, "rev-parse", "HEAD").strip()
        os.makedirs(os.path.join(gitdir, "rebase-merge"))
        write(os.path.join(gitdir, "HEAD"), head + "\n")

    def test_marks_of_a_parent_in_the_middle_of_an_operation_are_not_read(self):
        self.family(plan=["work-mr"])
        self.close_children(work=False)
        conf, _ = settings.load(self.root)
        self.plant_marks()
        self.busy()
        self.assert_marks_not_read(conf)

    def test_the_end_of_a_phase_is_not_marked_when_the_parent_is_not_settled(self):
        """フェーズの終わりのマーカーは書く側の置き場に置く。決まらなければ何も書かない。"""
        self.family(plan=["work-mr"])
        self.close_children(work=False)
        conf, _ = settings.load(self.root)
        git(self.parent_tree, "checkout", "--quiet", "-b", "other")
        copies, _ = approval.scan(conf, self.root)
        parent = approval_checks.by_id(copies)["i0001"]
        self.assertEqual(phase.announce(io.StringIO(), self.root, conf, parent), "")
        self.assertFalse(os.path.exists(approval.nowhere(conf, self.root, "i0001")))


if __name__ == "__main__":
    unittest.main()
