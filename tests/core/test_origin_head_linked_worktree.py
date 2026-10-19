"""`origin_head` は、リンクされたワークツリーからも、共通の git ディレクトリの origin/HEAD を読む。

統合先は origin/HEAD だけで決まる。ワークツリーの git ディレクトリには refs/remotes が無いので、
そこだけを読むと、ワークツリーを指す呼び出しでは統合先が空になる。
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest

from ccnavi.infra import tree
from tests import GIT_ENV


def git(cwd: str, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **GIT_ENV},
    )


class OriginHeadLinkedWorktreeTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = os.path.realpath(tmp.name)
        self.repo = os.path.join(base, "repo")
        os.makedirs(self.repo)
        git(self.repo, "init", "--quiet", "-b", "main")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "t")
        git(self.repo, "commit", "--quiet", "--allow-empty", "-m", "init")
        git(self.repo, "update-ref", "refs/remotes/origin/trunk", "HEAD")
        git(self.repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
        self.linked = os.path.join(base, "linked")
        git(self.repo, "worktree", "add", "--quiet", "-b", "work", self.linked)

    def test_the_main_tree_reads_its_own_origin_head(self) -> None:
        self.assertEqual("trunk", tree.origin_head(self.repo))

    def test_a_linked_worktree_reads_the_shared_origin_head(self) -> None:
        self.assertEqual("trunk", tree.origin_head(self.linked))

    def test_a_repository_without_origin_head_is_empty(self) -> None:
        git(self.repo, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
        self.assertEqual("", tree.origin_head(self.repo))
        self.assertEqual("", tree.origin_head(self.linked))


if __name__ == "__main__":
    unittest.main()
