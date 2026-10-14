"""親を閉じたときのワークツリーとブランチの片付け（`retire.py`）。

見るのは 3 つ。

1. `worktree retire`（ready が Draft を外した後）: 子のワークツリーを消し、合流した子のブランチは
   先頭が `merged_sha` と同じで、その sha が push 済みの親に含まれるときだけ消す。取り消した子は
   確認なしでは消さない。未コミットの変更や、git が無視しているファイルがあれば消さない
2. `worktree branch <名前> [confirmed]`: 記録した子のブランチだけを、条件を確かめ直して消す
3. `sync tidy`（MR がマージされたと分かった後）: 手元の先頭が MR の先頭に含まれるときだけ
   親のブランチを消す
"""

from __future__ import annotations

import json
import os

from tests.ticket.test_archive import ReadyHarness
from tests.ticket.test_ticket import git, write

CHILD = "i0001-01-01"


class RetireHarness(ReadyHarness):
    def closed_and_pushed(self):
        """親を閉じて ready を済ませ、退避の削除をコミットして push した状態。fixture を返す。"""
        fixture = self.closable()
        done = self.ready(fixture)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.commit_parent("退避")
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        return fixture

    def retire(self, *words):
        return self.ccnavi("--cwd", self.root, "worktree", *(words or ("retire", "i0001")))

    @property
    def child_tree(self):
        return os.path.join(self.root, ".claude", "worktrees", CHILD)

    def branches(self):
        return git(self.root, "branch", "--format=%(refname:short)").split()

    @property
    def record(self):
        return os.path.join(self.state, "retire", "i0001.json")

    def read_record(self):
        with open(self.record, encoding="utf-8") as f:
            return json.load(f)


class RetireAfterReadyTest(RetireHarness):
    def test_ready_notes_the_children_before_the_tickets_move_to_the_archive(self):
        self.closed_and_pushed()
        held = self.read_record()
        self.assertEqual(held["branch"], "i0001")
        merged = git(self.parent_tree, "rev-parse", CHILD).strip()
        self.assertEqual(
            [(c["ticket"], c["kind"], c["merged_sha"]) for c in held["children"]],
            [(CHILD, "merged", merged)],
        )

    def test_retire_removes_the_worktrees_and_the_merged_child_branch(self):
        self.closed_and_pushed()
        self.assertTrue(os.path.isdir(self.child_tree))
        done = self.retire()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(f"ワークツリー {CHILD} を削除しました", done.stdout)
        self.assertIn(f"ブランチ {CHILD} を削除しました", done.stdout)
        self.assertIn("ワークツリー i0001 を削除しました", done.stdout)
        self.assertFalse(os.path.exists(self.child_tree))
        self.assertFalse(os.path.exists(self.parent_tree))
        # 子のブランチは消え、親のブランチは MR のマージまで残る
        self.assertEqual(self.branches(), ["i0001", "main"])
        # 打ち直しても何も起きない
        again = self.retire()
        self.assertEqual((again.returncode, again.stdout.count("削除しました")), (0, 0))

    def test_a_child_branch_that_moved_after_the_merge_is_kept(self):
        self.closed_and_pushed()
        git(self.child_tree, "commit", "--quiet", "--allow-empty", "-m", "合流のあとの変更")
        done = self.retire()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("合流した先頭", done.stdout)
        self.assertIn(f"git -C {self.root} branch -D {CHILD}", done.stdout)
        self.assertIn(CHILD, self.branches())
        self.assertFalse(os.path.exists(self.child_tree))

    def test_a_merged_head_that_the_pushed_parent_lacks_keeps_the_branch(self):
        self.closed_and_pushed()
        git(self.root, "update-ref", "refs/remotes/origin/i0001", "main")
        done = self.retire()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("push 済みの親に含まれない", done.stdout)
        self.assertIn(CHILD, self.branches())

    def test_a_dirty_child_worktree_and_its_branch_stay(self):
        self.closed_and_pushed()
        write(os.path.join(self.child_tree, "memo.txt"), "書きかけ\n")
        done = self.retire()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("未コミットの変更があるので、何も削除しませんでした", done.stdout)
        self.assertIn("memo.txt", done.stdout)
        self.assertTrue(os.path.isdir(self.child_tree))
        self.assertIn("チェックアウトしている", done.stdout)
        self.assertIn(CHILD, self.branches())

    def test_ignored_files_keep_the_worktree_and_say_why(self):
        self.closed_and_pushed()
        exclude = git(self.child_tree, "rev-parse", "--git-path", "info/exclude").strip()
        exclude = exclude if os.path.isabs(exclude) else os.path.join(self.child_tree, exclude)
        write(exclude, ".env\n")
        write(os.path.join(self.child_tree, ".env"), "SECRET=1\n")
        done = self.retire()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("git が無視しているファイル", done.stdout)
        self.assertIn(".env", done.stdout)
        self.assertTrue(os.path.isfile(os.path.join(self.child_tree, ".env")))
        self.assertIn(CHILD, self.branches())


class CancelledChildTest(RetireHarness):
    """取り消した子のブランチは、確認なしでは消さない。"""

    def setUp(self):
        super().setUp()
        self.closed_and_pushed()
        git(self.root, "branch", "i0001-01-09", "main")
        held = self.read_record()
        held["children"].append(
            {"ticket": "i0001-01-09", "kind": "cancelled", "merged_sha": "", "project": ""}
        )
        write(self.record, json.dumps(held))

    def test_retire_lists_it_and_keeps_it(self):
        done = self.retire()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("取り消した子のブランチ", done.stdout)
        self.assertIn("ユーザに見せて", done.stdout)
        self.assertIn("- i0001-01-09", done.stdout)
        self.assertIn("ccnavi-clean.sh --branch i0001-01-09 --confirmed", done.stdout)
        self.assertIn("i0001-01-09", self.branches())
        # 合流した子と親のワークツリーは片付く
        self.assertNotIn(CHILD, self.branches())
        self.assertFalse(os.path.exists(self.parent_tree))

    def test_branch_without_confirmation_does_not_delete(self):
        refused = self.retire("branch", "i0001-01-09")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn("ユーザに消してよいか確かめてから", refused.stderr)
        self.assertIn("i0001-01-09", self.branches())

    def test_branch_with_confirmation_deletes(self):
        done = self.retire("branch", "i0001-01-09", "confirmed")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("ブランチ i0001-01-09 を削除しました", done.stdout)
        self.assertNotIn("i0001-01-09", self.branches())

    def test_a_branch_that_was_not_recorded_is_never_deleted(self):
        git(self.root, "branch", "other", "main")
        refused = self.retire("branch", "other", "confirmed")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn("記録したブランチではない", refused.stderr)
        self.assertIn("other", self.branches())


class TidyAfterMergeTest(RetireHarness):
    """MR がマージされたと分かった後の片付け（親のブランチと取り込み記録）。"""

    def tidy(self, sha):
        return self.ccnavi("--cwd", self.root, "sync", "tidy", "i0001", "self", "i0001", sha)

    def pushed_head(self):
        return git(self.root, "rev-parse", "refs/remotes/origin/i0001").strip()

    def test_the_parent_branch_the_worktree_and_the_note_are_removed(self):
        self.closed_and_pushed()
        done = self.tidy(self.pushed_head())
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("ワークツリー i0001 を削除しました", done.stdout)
        self.assertIn("ブランチ i0001 を削除しました", done.stdout)
        self.assertIn(f"ブランチ {CHILD} を削除しました", done.stdout)
        self.assertEqual(done.stdout.splitlines()[-1], "record yes")
        self.assertEqual(self.branches(), ["main"])
        self.assertFalse(os.path.exists(self.record))

    def test_an_unpushed_commit_keeps_the_branch(self):
        self.closed_and_pushed()
        head = self.pushed_head()
        git(self.parent_tree, "commit", "--quiet", "--allow-empty", "-m", "未 push")
        done = self.tidy(head)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("MR の先頭", done.stdout)
        self.assertIn("未 push のコミットがある", done.stdout)
        self.assertIn(f"git -C {self.root} branch -D i0001", done.stdout)
        self.assertIn("i0001", self.branches())
        self.assertEqual(done.stdout.splitlines()[-1], "record no")
        self.assertTrue(os.path.exists(self.record))

    def test_uncommitted_changes_keep_the_worktree_and_the_branch(self):
        self.closed_and_pushed()
        write(os.path.join(self.parent_tree, "memo.txt"), "書きかけ\n")
        done = self.tidy(self.pushed_head())
        self.assertIn("未コミットの変更があるので、何も削除しませんでした", done.stdout)
        self.assertTrue(os.path.isdir(self.parent_tree))
        self.assertIn("チェックアウトしている", done.stdout)
        self.assertIn("i0001", self.branches())
        self.assertEqual(done.stdout.splitlines()[-1], "record no")

    def test_a_head_that_is_not_here_is_not_guessed(self):
        self.closed_and_pushed()
        done = self.tidy("0123456789abcdef0123456789abcdef01234567")
        self.assertIn("手元に無く、含まれるか確かめられない", done.stdout)
        self.assertIn("i0001", self.branches())
        self.assertEqual(done.stdout.splitlines()[-1], "record no")

    def test_the_head_of_the_branch_itself_does_not_count_as_merged(self):
        # 呼び手が渡す sha を信じると、未 push のブランチの先頭そのものを渡せば消せる。
        self.closed_and_pushed()
        git(self.parent_tree, "commit", "--quiet", "--allow-empty", "-m", "未 push")
        own = git(self.parent_tree, "rev-parse", "HEAD").strip()
        done = self.tidy(own)
        self.assertIn("未 push のコミットがある", done.stdout)
        self.assertIn("origin/i0001", done.stdout)
        self.assertIn("i0001", self.branches())
        self.assertEqual(done.stdout.splitlines()[-1], "record no")

    def test_a_branch_without_a_pushed_head_is_kept(self):
        self.closed_and_pushed()
        head = self.pushed_head()
        git(self.root, "update-ref", "-d", "refs/remotes/origin/i0001")
        done = self.tidy(head)
        self.assertIn("手元に無く、未 push のコミットが無いか確かめられない", done.stdout)
        self.assertIn("i0001", self.branches())

    def test_only_the_branch_of_the_parent_is_tidied(self):
        # 親の識別子と別のブランチを渡しても消さない。
        self.closed_and_pushed()
        git(self.root, "branch", "other", "main")
        sha = git(self.root, "rev-parse", "other").strip()
        refused = self.ccnavi("--cwd", self.root, "sync", "tidy", "i0001", "self", "other", sha)
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn("親 i0001 のブランチではない", refused.stderr)
        self.assertIn("other", self.branches())

    def test_a_bad_argument_is_refused(self):
        self.closed_and_pushed()
        refused = self.tidy("not-a-sha")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertIn("i0001", self.branches())
