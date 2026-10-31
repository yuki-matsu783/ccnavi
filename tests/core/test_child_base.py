"""子の仕事の始まり（`gitstate.child_base`）。

親のワークツリーの HEAD と子の HEAD の共通の祖先から子の先頭までを、子の範囲の検査と
リスクの計測の対象にする。着手の基準点から数えると、衝突を解くために子へ親を取り込んだとき、
親に入っているほかの子の変更が子の変更に混ざる。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from ccnavi.infra import gitstate


def git(cwd, *args):
    done = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def commit(tree, name, text="x\n"):
    with open(os.path.join(tree, name), "w", encoding="utf-8") as f:
        f.write(text)
    git(tree, "add", "--", name)
    git(tree, "commit", "-q", "-m", name)


@unittest.skipIf(shutil.which("git") is None, "git が無い")
class ChildBaseTest(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.parent = os.path.join(self.base, "parent")
        os.makedirs(self.parent)
        git(self.parent, "init", "-q", "-b", "p")
        commit(self.parent, "seed")
        self.start = git(self.parent, "rev-parse", "HEAD")
        self.child = os.path.join(self.base, "child")
        git(self.parent, "worktree", "add", "-q", "-b", "c", self.child, "p")

    def changed(self, base):
        return git(self.child, "diff", "--name-only", f"{base}..HEAD").split()

    def test_it_starts_where_the_child_left_the_parent(self):
        commit(self.child, "mine")
        base, why = gitstate.child_base(self.child, self.parent)
        self.assertEqual((base, why), (self.start, ""))

    def test_a_parent_that_moved_on_does_not_move_the_start(self):
        commit(self.child, "mine")
        commit(self.parent, "other")
        base, _ = gitstate.child_base(self.child, self.parent)
        self.assertEqual(base, self.start)
        self.assertEqual(self.changed(base), ["mine"])

    def test_taking_the_parent_in_does_not_mix_in_the_other_childs_changes(self):
        commit(self.child, "mine")
        commit(self.parent, "other")
        git(self.child, "merge", "-q", "--no-edit", "p")
        commit(self.child, "mine2")
        base, _ = gitstate.child_base(self.child, self.parent)
        # 着手の基準点から数えると、親から取り込んだ other が混ざる。
        self.assertIn("other", self.changed(self.start))
        # 親に入ったあとも、子が親を取り込む前に積んだ変更は子のものとして残る。
        self.assertEqual(self.changed(base), ["mine", "mine2"])

    def test_a_child_already_in_the_parent_starts_at_its_merged_head(self):
        commit(self.child, "mine")
        git(self.parent, "merge", "-q", "--no-ff", "--no-edit", "c")
        commit(self.child, "again")
        base, _ = gitstate.child_base(self.child, self.parent)
        self.assertEqual(self.changed(base), ["again"])

    def upstream(self, **files):
        """統合先（origin/main）を、着手のときの親の先頭から進める。親のブランチは動かさない。"""
        up = os.path.join(self.base, "up")
        git(self.parent, "worktree", "add", "-q", "-b", "m", up, self.start)
        for name, text in files.items():
            commit(up, name, text)
        git(self.parent, "update-ref", "refs/remotes/origin/m", git(up, "rev-parse", "HEAD"))
        return up

    def test_paths_only_taken_from_the_integration_branch_are_not_the_childs(self):
        self.upstream(theirs="t\n")
        commit(self.child, "mine")
        git(self.child, "merge", "-q", "--no-edit", "origin/m")
        base, _ = gitstate.child_base(self.child, self.parent)
        self.assertEqual(sorted(self.changed(base)), ["mine", "theirs"])
        self.assertEqual(gitstate.taken_from_remote(self.child, base), {"theirs"})

    def test_a_conflict_resolution_is_still_the_childs(self):
        self.upstream(seed="theirs\n")
        commit(self.child, "seed", "mine\n")
        done = subprocess.run(
            ["git", "merge", "--no-edit", "origin/m"], cwd=self.child, capture_output=True
        )
        self.assertNotEqual(done.returncode, 0)
        commit_text = "resolved\n"
        with open(os.path.join(self.child, "seed"), "w", encoding="utf-8") as f:
            f.write(commit_text)
        git(self.child, "add", "--", "seed")
        git(self.child, "commit", "-q", "--no-edit")
        base, _ = gitstate.child_base(self.child, self.parent)
        self.assertEqual(gitstate.taken_from_remote(self.child, base), set())

    def test_a_path_the_child_changed_after_taking_it_in_is_the_childs(self):
        self.upstream(theirs="t\n", other="o\n")
        commit(self.child, "mine")
        git(self.child, "merge", "-q", "--no-edit", "origin/m")
        commit(self.child, "theirs", "edited\n")
        base, _ = gitstate.child_base(self.child, self.parent)
        self.assertEqual(gitstate.taken_from_remote(self.child, base), {"other"})

    def test_a_merge_of_a_branch_not_on_the_remote_is_not_taken(self):
        self.upstream(theirs="t\n")
        git(self.parent, "update-ref", "-d", "refs/remotes/origin/m")
        commit(self.child, "mine")
        git(self.child, "merge", "-q", "--no-edit", "m")
        base, _ = gitstate.child_base(self.child, self.parent)
        self.assertEqual(gitstate.taken_from_remote(self.child, base), set())

    def test_the_risk_measure_does_not_count_the_taken_paths(self):
        from ccnavi.tickets.config import risk

        self.upstream(theirs="t\n" * 50)
        commit(self.child, "mine")
        git(self.child, "merge", "-q", "--no-edit", "origin/m")
        base, _ = gitstate.child_base(self.child, self.parent)
        diff, why = risk.measure(self.child, base, "HEAD")
        self.assertEqual(why, "")
        self.assertEqual([c.path for c in diff.changes], ["mine"])

    def test_unreadable_trees_give_a_reason(self):
        base, why = gitstate.child_base(self.child, os.path.join(self.base, "nowhere"))
        self.assertEqual(base, "")
        self.assertTrue(why)


if __name__ == "__main__":
    unittest.main()
