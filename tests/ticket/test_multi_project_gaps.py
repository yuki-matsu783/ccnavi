"""複数プロジェクト（`projects/<名前>/`）の親子で、手動移動の判定が使う置き場とツリーが正しく決まるか。

プロジェクトの親のワークツリーも、ワークスペースの `.claude/worktrees/<親>` に置かれる
（元リポジトリがプロジェクト）。次の 3 つを仮のツリーで確かめる。

1. ワークツリーの持ち主がプロジェクトと決まる
2. 受け入れ台帳・マーカーの置き場（`approval.home_dir`）が、プロジェクトの親のワークツリーの中になる
3. `manual_settle._shared` が、プロジェクトの履歴と `origin/<親のブランチ>` で共有済みかを見る
"""

from __future__ import annotations

import os
import shutil
import tempfile
import types
import unittest

from ccnavi.infra import settings, tree
from ccnavi.tickets.approval import store as approval
from ccnavi.tickets.phase import manual_settle
from tests.ticket.test_ticket import git, write

PARENT = "feature-1-web-x"
CHILD = "feature-1-web-x-01-01"


class MultiProjectManualSettleTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-multi-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.root = os.path.realpath(self.root)
        git(self.root, "init", "--quiet", "-b", "main")
        write(os.path.join(self.root, ".gitignore"), ".claude/\nprojects/\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "--quiet", "-m", "init")
        self.project = os.path.join(self.root, "projects", "web")
        os.makedirs(self.project)
        git(self.project, "init", "--quiet", "-b", "main")
        write(os.path.join(self.project, "a.txt"), "a\n")
        git(self.project, "add", "-A")
        git(self.project, "commit", "--quiet", "-m", "init")
        self.remote = os.path.join(self.root, "remote.git")
        git(self.root, "init", "--quiet", "--bare", self.remote)
        git(self.project, "remote", "add", "origin", self.remote)
        git(self.project, "push", "--quiet", "origin", "main")
        self.wt = os.path.join(self.root, ".claude", "worktrees", PARENT)
        git(self.project, "worktree", "add", "--quiet", self.wt, "-b", PARENT, "main")
        git(self.wt, "push", "--quiet", "-u", "origin", PARENT)
        self.conf, _ = settings.load(self.root)

    def moved_child(self):
        path = write(os.path.join(self.wt, ".ccnavi", "approved", "done", f"{CHILD}.md"), "x\n")
        return types.SimpleNamespace(ticket=CHILD, path=path)

    def test_the_worktree_of_a_project_parent_belongs_to_the_project(self):
        found = {w.name: w.project for w in tree.worktrees(self.root, self.conf.projects)}
        self.assertEqual(found, {PARENT: "web"})

    def test_the_ledger_home_of_a_project_parent_is_inside_its_worktree(self):
        home = approval.home_dir(self.conf, self.root, PARENT, "", project="web")
        self.assertEqual(
            os.path.normcase(home),
            os.path.normcase(settings.approved_dir(self.conf, self.wt)),
        )
        # リポジトリを渡さなくても、同じ名前の親のワークツリーが 1 つなら同じ場所になる
        self.assertEqual(approval.home_dir(self.conf, self.root, PARENT, ""), home)

    def test_a_move_in_the_project_repository_is_judged_by_its_own_history(self):
        parent = types.SimpleNamespace(ticket=PARENT)
        child = self.moved_child()
        self.assertEqual(manual_settle._shared(self.root, parent, [child]), "unshared")
        git(self.wt, "add", "-A", "-f")
        git(self.wt, "commit", "--quiet", "-m", "move")
        self.assertEqual(manual_settle._shared(self.root, parent, [child]), "unshared")
        git(self.wt, "push", "--quiet", "origin", PARENT)
        shared = manual_settle._shared(self.root, parent, [child])
        self.assertIsInstance(shared, float)
        self.assertGreater(shared, 0)


if __name__ == "__main__":
    unittest.main()
