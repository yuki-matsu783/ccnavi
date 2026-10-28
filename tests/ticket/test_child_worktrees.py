"""承認した子のワークツリーの計画（`worktrees.child_worktrees`）の状態を確かめる。

作成（`create`）は親のワークツリーが git の登録された本物のワークツリーであることを要るので、
`test_approve_json` のフィクスチャ（本物の git を使う）で見る。ここは git が要らない状態を見る。
"""

from __future__ import annotations

import os
import tempfile
import unittest

from ccnavi.tickets import worktrees
from ccnavi.tickets.model import ticket_model


def child(name: str, parent: str, project: str = "") -> ticket_model.Ticket:
    return ticket_model.Ticket(ticket=name, parent=parent, project=project)


class ChildWorktreesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        os.makedirs(os.path.join(self.root, ".claude", "worktrees"))

    def tearDown(self):
        self.tmp.cleanup()

    def wt(self, name: str) -> str:
        return os.path.join(self.root, ".claude", "worktrees", name)

    def test_parent_worktree_missing_is_no_parent(self):
        plan = worktrees.child_worktrees(self.root, [child("i1-01", "i1")])
        self.assertEqual(plan[0]["state"], worktrees.CHILD_NO_PARENT)
        self.assertEqual(plan[0]["path"], ".claude/worktrees/i1-01")
        self.assertEqual(plan[0]["base"], "")

    def test_existing_child_worktree_is_left_alone(self):
        os.makedirs(self.wt("i1-01"))
        plan = worktrees.child_worktrees(self.root, [child("i1-01", "i1")])
        self.assertEqual(plan[0]["state"], worktrees.CHILD_EXISTS)

    def test_project_child_is_never_created_by_approval(self):
        plan = worktrees.child_worktrees(self.root, [child("i1-01", "i1", project="p")])
        self.assertEqual(plan[0]["state"], worktrees.CHILD_PROJECT)

    def test_parent_directory_that_is_not_a_worktree_is_no_parent(self):
        os.makedirs(self.wt("i1"))  # .git が無い、ただのディレクトリ
        plan = worktrees.child_worktrees(self.root, [child("i1-01", "i1")])
        self.assertEqual(plan[0]["state"], worktrees.CHILD_NO_PARENT)

    def test_parents_are_not_planned(self):
        plan = worktrees.child_worktrees(self.root, [ticket_model.Ticket(ticket="i1", parent="")])
        self.assertEqual(plan, [])


if __name__ == "__main__":
    unittest.main()
