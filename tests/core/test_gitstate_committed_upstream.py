"""`gitstate.committed` が、上流から取り込んだコミットを自分の変更として数えないことのテスト。

fast-forward（`pull --ff-only`・`checkout <上流>`・`reset --hard <上流>`）は上流の通常コミットを
first-parent の列に入れる。リモート追跡ブランチに入っているコミットは外し、未 push のものだけ数える。
"""

import os
import shutil
import subprocess
import tempfile
import unittest

from ccnavi.infra import gitstate


def git(repo, *args):
    done = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if done.returncode != 0:
        raise AssertionError(f"git {args} failed: {done.stderr}")
    return done.stdout.strip()


def commit_file(repo, rel, text="x\n"):
    with open(os.path.join(repo, rel), "w", encoding="utf-8", newline="") as f:
        f.write(text)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", f"add {rel}")


class CommittedUpstreamTest(unittest.TestCase):
    def setUp(self):
        root = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        self.up = os.path.join(root, "up")
        self.clone = os.path.join(root, "clone")
        os.mkdir(self.up)
        git(self.up, "init", "-q", "-b", "main")
        commit_file(self.up, "a")
        subprocess.run(
            ["git", "clone", "-q", self.up, self.clone], check=True, capture_output=True
        )
        self.base = git(self.clone, "rev-parse", "HEAD")
        commit_file(self.up, "b")

    def paths(self, repo=None, base=None):
        changes, reason = gitstate.committed(repo or self.clone, base or self.base)
        self.assertEqual(reason, "")
        return {c.path for c in changes}

    def test_pull_ff_only_is_not_counted(self):
        git(self.clone, "pull", "-q", "--ff-only")
        self.assertEqual(self.paths(), set())

    def test_checkout_remote_branch_is_not_counted(self):
        git(self.clone, "fetch", "-q", "origin")
        git(self.clone, "checkout", "-q", "origin/main")
        self.assertEqual(self.paths(), set())

    def test_reset_hard_to_remote_is_not_counted(self):
        git(self.clone, "fetch", "-q", "origin")
        git(self.clone, "reset", "-q", "--hard", "origin/main")
        self.assertEqual(self.paths(), set())

    def test_unpushed_local_commit_is_counted(self):
        commit_file(self.clone, "mine")
        self.assertEqual(self.paths(), {"mine"})

    def test_local_commit_after_pull_is_counted_alone(self):
        git(self.clone, "pull", "-q", "--ff-only")
        commit_file(self.clone, "mine")
        self.assertEqual(self.paths(), {"mine"})

    def test_commit_tree_then_reset_hard_is_counted(self):
        with open(os.path.join(self.clone, "forged"), "w") as f:
            f.write("x\n")
        git(self.clone, "add", ".")
        tree = git(self.clone, "write-tree")
        sha = git(self.clone, "commit-tree", tree, "-p", self.base, "-m", "forged")
        git(self.clone, "reset", "-q", "--hard", sha)
        self.assertEqual(self.paths(), {"forged"})

    def test_merge_pull_is_not_counted(self):
        commit_file(self.clone, "mine")
        base = git(self.clone, "rev-parse", "HEAD")
        git(self.clone, "pull", "-q", "--no-rebase", "--no-edit")
        self.assertEqual(self.paths(base=base), set())

    def test_rebase_pull_leaves_only_unpushed(self):
        commit_file(self.clone, "mine")
        git(self.clone, "pull", "-q", "--rebase")
        self.assertEqual(self.paths(), {"mine"})

    def test_without_remote_behaves_as_before(self):
        repo = os.path.join(os.path.dirname(self.up), "solo")
        os.mkdir(repo)
        git(repo, "init", "-q", "-b", "main")
        commit_file(repo, "a")
        base = git(repo, "rev-parse", "HEAD")
        commit_file(repo, "c")
        self.assertEqual(self.paths(repo, base), {"c"})


if __name__ == "__main__":
    unittest.main()
