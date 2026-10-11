"""実行後チェックが、`ccnavi-git.sh integrate` の残した staged を外す条件のテスト。

本物の git リポジトリを一時ディレクトリに作る。記録は `.git` の中にありエージェントにも書けるので、
記録の内容を信用せず git の状態で確かめていること（偽の記録で任意の staged を通せない）が要点。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from ccnavi.hook import post_findings
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


def write(repo, rel, text):
    path = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def merge_tree_available():
    done = subprocess.run(
        ["git", "merge-tree", "--write-tree", "HEAD", "HEAD"], capture_output=True, text=True
    )
    return done.returncode == 0


def change(repo, rel):
    return gitstate.Change(
        kind="modified",
        path=rel,
        full=os.path.realpath(os.path.join(repo, rel)),
        status="M ",
        staged=True,
    )


@unittest.skipUnless(merge_tree_available(), "git merge-tree --write-tree (git 2.38+) is needed")
class IntegrateWritesTest(unittest.TestCase):
    def setUp(self):
        self.repo = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo, True)
        git(self.repo, "init", "-q", "-b", "main")
        write(self.repo, "a.txt", "a\n")
        write(self.repo, "gone.txt", "gone\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "init")
        git(self.repo, "switch", "-q", "-c", "feat")
        write(self.repo, "a.txt", "a2\n")
        write(self.repo, "new.txt", "new\n")
        git(self.repo, "rm", "-q", "gone.txt")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "work")
        self.tip = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "switch", "-q", "main")
        self.base = git(self.repo, "rev-parse", "HEAD")
        # integrate が取り込むのは `.claude/worktrees/<名前>` のワークツリーのブランチ。
        git(self.repo, "worktree", "add", "-q", ".claude/worktrees/feat", "feat")
        git(self.repo, "merge", "-q", "--squash", "feat")
        self.tree = git(self.repo, "write-tree")
        self.changes = [change(self.repo, p) for p in ("a.txt", "new.txt", "gone.txt")]

    def mark(self, **over):
        fields = {
            "state": "staged",
            "branch": "feat",
            "branch_sha": self.tip,
            "base": self.base,
            "tree": self.tree,
        }
        fields.update(over)
        gdir = os.path.join(self.repo, ".git", "ccnavi-integrate", "feat")
        os.makedirs(gdir, exist_ok=True)
        with open(os.path.join(gdir, "marker"), "w", encoding="utf-8") as f:
            f.writelines(f"{k} {v}\n" for k, v in fields.items())

    def full(self, *rels):
        return {os.path.realpath(os.path.join(self.repo, r)) for r in rels}

    def test_real_squash_with_record_is_dropped(self):
        self.mark()
        got = post_findings._integrate_writes(self.changes, self.repo)
        self.assertEqual(got, self.full("a.txt", "new.txt", "gone.txt"))

    def test_no_record_is_reported(self):
        self.assertEqual(post_findings._integrate_writes(self.changes, self.repo), set())

    def test_conflict_state_is_reported(self):
        self.mark(state="conflict")
        self.assertEqual(post_findings._integrate_writes(self.changes, self.repo), set())

    def test_forged_tree_is_reported(self):
        self.mark(tree="0" * 40)
        self.assertEqual(post_findings._integrate_writes(self.changes, self.repo), set())

    def test_head_moved_is_reported(self):
        self.mark(base="0" * 40)
        self.assertEqual(post_findings._integrate_writes(self.changes, self.repo), set())

    def test_branch_moved_is_reported(self):
        self.mark(branch_sha="0" * 40)
        self.assertEqual(post_findings._integrate_writes(self.changes, self.repo), set())

    def test_extra_staged_file_after_integrate_is_reported(self):
        self.mark()
        write(self.repo, "evil.txt", "x\n")
        git(self.repo, "add", "evil.txt")
        changes = [*self.changes, change(self.repo, "evil.txt")]
        self.assertEqual(post_findings._integrate_writes(changes, self.repo), set())

    def test_forged_record_matching_a_non_squash_index_is_reported(self):
        # 記録の tree を今の index に合わせても、index が squash の結果でなければ外れない。
        git(self.repo, "reset", "-q", "--hard", "HEAD")
        write(self.repo, "evil.txt", "x\n")
        git(self.repo, "add", "evil.txt")
        self.tree = git(self.repo, "write-tree")
        self.mark()
        changes = [change(self.repo, "evil.txt")]
        self.assertEqual(post_findings._integrate_writes(changes, self.repo), set())

    def test_branch_without_worktree_is_reported(self):
        # ワークツリーを通っていないブランチの squash は、偽の記録で外せない。
        git(self.repo, "reset", "-q", "--hard", "HEAD")
        git(self.repo, "worktree", "remove", "--force", ".claude/worktrees/feat")
        git(self.repo, "merge", "-q", "--squash", "feat")
        self.tree = git(self.repo, "write-tree")
        self.mark()
        self.assertEqual(post_findings._integrate_writes(self.changes, self.repo), set())

    def test_worktree_on_other_commit_is_reported(self):
        self.mark()
        wt = os.path.join(self.repo, ".claude", "worktrees", "feat")
        write(wt, "later.txt", "x\n")
        git(wt, "add", "later.txt")
        git(wt, "commit", "-q", "-m", "later")
        self.assertEqual(post_findings._integrate_writes(self.changes, self.repo), set())

    def test_ticket_bound_name_is_reported(self):
        self.mark()
        write(self.repo, ".ccnavi/approved/doing/feat.md", "---\nticket: feat\n---\n")
        self.assertEqual(post_findings._integrate_writes(self.changes, self.repo), set())

    def test_committed_changes_are_not_dropped(self):
        # コミット済みの差分を見る経路は top を渡さない。そこでは外さない。
        self.mark()
        self.assertEqual(post_findings._integrate_writes(self.changes, ""), set())

    def test_unstaged_change_is_not_dropped(self):
        self.mark()
        changes = [
            gitstate.Change(kind=c.kind, path=c.path, full=c.full, status=" M", staged=False)
            for c in self.changes
        ]
        self.assertEqual(post_findings._integrate_writes(changes, self.repo), set())

    def test_rename_in_the_squash_drops_both_sides(self):
        git(self.repo, "reset", "-q", "--hard", "HEAD")
        git(self.repo, "worktree", "remove", "--force", ".claude/worktrees/feat")
        git(self.repo, "branch", "-q", "-D", "feat")
        git(self.repo, "switch", "-q", "-c", "ren")
        git(self.repo, "mv", "a.txt", "b.txt")
        git(self.repo, "commit", "-q", "-m", "rename")
        self.tip = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "switch", "-q", "main")
        git(self.repo, "worktree", "add", "-q", ".claude/worktrees/ren", "ren")
        git(self.repo, "merge", "-q", "--squash", "ren")
        self.tree = git(self.repo, "write-tree")
        self.mark(branch="ren")
        gdir = os.path.join(self.repo, ".git", "ccnavi-integrate")
        os.replace(os.path.join(gdir, "feat"), os.path.join(gdir, "ren"))
        changes = [change(self.repo, "a.txt"), change(self.repo, "b.txt")]
        got = post_findings._integrate_writes(changes, self.repo)
        self.assertEqual(got, self.full("a.txt", "b.txt"))

    def test_record_for_other_name_is_reported(self):
        self.mark(branch="other")
        self.assertEqual(post_findings._integrate_writes(self.changes, self.repo), set())

    def test_worktree_edit_on_a_staged_path_is_kept(self):
        self.mark()
        write(self.repo, "a.txt", "edited after integrate\n")
        got = post_findings._integrate_writes(self.changes, self.repo)
        self.assertEqual(got, self.full("new.txt", "gone.txt"))

    def test_unrelated_change_is_kept(self):
        self.mark()
        write(self.repo, "other.txt", "x\n")
        changes = [*self.changes, change(self.repo, "other.txt")]
        got = post_findings._integrate_writes(changes, self.repo)
        self.assertNotIn(os.path.realpath(os.path.join(self.repo, "other.txt")), got)
        self.assertEqual(got, self.full("a.txt", "new.txt", "gone.txt"))


if __name__ == "__main__":
    unittest.main()
