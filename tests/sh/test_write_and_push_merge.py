"""write-and-push の受入テスト。取り込み（merge）まわりの前半。

共通の道具と全体の説明は `tests/sh/write_and_push_harness.py` にある。
"""

from __future__ import annotations

import os
import unittest

from tests.sh.write_and_push_harness import (
    APPROVED,
    CHILD,
    CHILD_B,
    PARENT,
    WriteAndPushMergeHarness,
    git,
    write,
)


class WriteAndPushMergeTest(WriteAndPushMergeHarness):
    """取り込み（merge）まわりの前半。"""

    # ---- 1. 合流と状態は 1 つのマージコミット

    def test_finish_merges_the_child_in_one_merge_commit_and_pushes(self):
        self.start_parent()
        tree = self.start_child()
        child_head = self.work(tree, "wip/design/a.md", "a\n")
        before = self.sha(self.tree, "HEAD")
        result = self.ticket("finish", CHILD)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        head = self.sha(self.tree, "HEAD")
        parents = git(self.tree, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
        self.assertEqual(parents[1:], [before, child_head])
        self.assertEqual(self.remote_sha(), head)
        self.assertEqual(self.dirty(), "")
        self.assertFalse(self.merging())
        changed = git(
            self.tree, "diff", "--name-only", "--no-renames", "HEAD^1", "HEAD"
        ).stdout.split()
        self.assertIn("wip/design/a.md", changed)
        self.assertIn(f"{APPROVED}/doing/{CHILD}.md", changed)
        self.assertIn(f"wip/proposals/review/{CHILD}.md", changed)
        review = self.read(os.path.join(self.tree, "wip", "proposals", "review", CHILD + ".md"))
        self.assertIn(f'merged_sha: "{child_head}"', review)
        # 子の先頭は親の先頭の祖先
        self.assertEqual(
            git(
                self.tree, "merge-base", "--is-ancestor", child_head, "HEAD", check=False
            ).returncode,
            0,
        )

    def test_a_child_already_in_the_parent_is_not_merged_again(self):
        self.start_parent()
        tree = self.start_child()
        self.work(tree, "wip/design/a.md", "a\n")
        git(self.tree, "merge", "-q", "--no-edit", CHILD)
        git(self.tree, "push", "-q", "origin", PARENT)
        result = self.ticket("finish", CHILD)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        parents = git(self.tree, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
        self.assertEqual(len(parents), 2, "合流するものが無いので、ふつうのコミット")
        self.assertIn(
            f'merged_sha: "{self.sha(tree, "HEAD")}"',
            self.read(os.path.join(self.tree, "wip", "proposals", "review", CHILD + ".md")),
        )

    # ---- 2. 別の PC の子 2

    def test_a_second_child_sees_the_first_and_reaches_the_request(self):
        self.start_parent()
        self.approve_second_child()
        first = self.start_child()
        first_head = self.work(first, "wip/design/a.md", "a\n")
        self.assertEqual(self.ticket("finish", CHILD).returncode, 0)
        second = self.start_child(CHILD_B)
        self.assertTrue(os.path.isfile(os.path.join(second, "wip", "design", "a.md")))
        second_head = self.work(second, "wip/design/b.md", "b\n")
        result = self.ticket("finish", CHILD_B)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        head = self.sha(self.tree, "HEAD")
        self.assertEqual(self.remote_sha(), head)
        for sha in (first_head, second_head):
            done = git(self.tree, "merge-base", "--is-ancestor", sha, "HEAD", check=False)
            self.assertEqual(done.returncode, 0)
        body = write(os.path.join(self._base, "body.md"), "見てほしい\n")
        prepared = self.exe(
            "--cwd", self.tree, "--phase", "1", "--body-file", body, "review", "prepare"
        )
        self.assertNotIn("合流", prepared.stderr, prepared.stderr)

    def test_a_commit_in_a_review_childs_worktree_stops_the_request(self):
        """review/ の子のワークツリーで後から積んだコミットは合流されないので、依頼を止める。"""
        self.start_parent()
        tree = self.start_child()
        self.work(tree, "wip/design/a.md", "a\n")
        self.assertEqual(self.ticket("finish", CHILD).returncode, 0)
        self.assertTrue(os.path.isdir(tree), "review/ の子のワークツリーが残っていない")
        body = write(os.path.join(self._base, "body.md"), "見てほしい\n")
        args = ("--cwd", self.tree, "--phase", "1", "--body-file", body, "review", "prepare")
        self.assertNotIn("合流されていない修正", self.exe(*args).stderr)
        late = self.work(tree, "wip/design/a.md", "fixed\n", "fix after finish")
        prepared = self.exe(*args)
        self.assertNotEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
        self.assertIn(f"子 {CHILD} の手元のブランチに、合流されていない修正がある", prepared.stderr)
        self.assertIn(late[:12], prepared.stderr)

    # ---- 3. 止まる

    def test_a_ticket_place_in_the_childs_commits_stops_without_merging(self):
        self.start_parent()
        tree = self.start_child()
        self.work(tree, "wip/proposals/todo/evil.md", "x\n")
        before = self.sha(self.tree, "HEAD")
        result = self.ticket("finish", CHILD)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("置き場の変更", result.stderr)
        self.assertIn("wip/proposals/todo/evil.md", result.stderr)
        self.assert_untouched(before)

    def test_a_path_outside_the_scope_stops_without_merging(self):
        self.start_parent()
        tree = self.start_child()
        self.work(tree, "src/x.py", "x = 1\n")
        before = self.sha(self.tree, "HEAD")
        result = self.ticket("finish", CHILD)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("範囲の外のパス", result.stderr)
        self.assertIn("src/x.py", result.stderr)
        self.assert_untouched(before)

    def test_a_conflict_is_aborted_and_changes_nothing(self):
        self.start_parent()
        tree = self.start_child()
        self.work(tree, "wip/design/a.md", "child\n")
        remote = self.remote_commit("wip/design/a.md", "remote\n")
        result = self.ticket("finish", CHILD)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("衝突する", result.stderr)
        self.assertIn("wip/design/a.md", result.stderr)
        self.assertIn(f"子のワークツリー（.claude/worktrees/{CHILD}）", result.stderr)
        # 取り込みは済んでいるが、合流は何も残していない
        self.assert_untouched(remote)

    def test_a_conflict_names_the_child_that_merged_first(self):
        self.start_parent()
        self.approve_second_child()
        first = self.start_child()
        second = self.start_child(CHILD_B)
        self.work(first, "wip/design/a.md", "first\n")
        self.work(second, "wip/design/a.md", "second\n")
        self.assertEqual(self.ticket("finish", CHILD).returncode, 0)
        before = self.sha(self.tree, "HEAD")
        result = self.ticket("finish", CHILD_B)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"先に合流した子 {CHILD}", result.stderr)
        self.assertIn(f"後から finish した {CHILD_B}", result.stderr)
        self.assert_untouched(before, CHILD_B)

    def test_a_conflict_on_paths_with_spaces_and_japanese_names_the_child_that_merged_first(self):
        self.start_parent()
        self.approve_second_child()
        first = self.start_child()
        second = self.start_child(CHILD_B)
        rels = ("wip/design/a b.md", "wip/design/日本.md")
        for tree, text in ((first, "first\n"), (second, "second\n")):
            for rel in rels:
                write(os.path.join(tree, *rel.split("/")), text)
            git(tree, "add", "-A")
            git(tree, "commit", "-q", "-m", "work")
        self.assertEqual(self.ticket("finish", CHILD).returncode, 0)
        before = self.sha(self.tree, "HEAD")
        result = self.ticket("finish", CHILD_B)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("wip/design/a b.md, wip/design/日本.md", result.stderr)
        for rel in rels:
            self.assertIn(f"先に合流した子 {CHILD} の変更と重なる（{rel}）", result.stderr)
        self.assert_untouched(before, CHILD_B)

    def test_a_path_ignored_in_the_parent_stops_without_overwriting_it(self):
        self.start_parent()
        tree = self.start_child()
        common = git(self.tree, "rev-parse", "--git-common-dir").stdout.strip()
        common = common if os.path.isabs(common) else os.path.join(self.tree, common)
        write(os.path.join(common, "info", "exclude"), "wip/design/secret.md\n")
        secret = write(os.path.join(self.tree, "wip", "design", "secret.md"), "USER SECRET\n")
        write(os.path.join(tree, "wip", "design", "secret.md"), "child\n")
        git(tree, "add", "-f", "wip/design/secret.md")
        git(tree, "commit", "-q", "-m", "work")
        before = self.sha(self.tree, "HEAD")
        result = self.ticket("finish", CHILD)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("git の無視するファイル", result.stderr)
        self.assertIn("wip/design/secret.md", result.stderr)
        self.assertEqual(self.read(secret), "USER SECRET\n")
        self.assert_untouched(before)

    def test_a_path_with_a_newline_stops_without_merging(self):
        self.start_parent()
        tree = self.start_child()
        self.work(tree, "wip/design/a\nb.md", "x\n")
        self.assert_unrestorable_stops("改行を含むパス")

    def test_a_rename_that_only_changes_case_stops_without_merging(self):
        self.start_parent()
        self.work(self.tree, "wip/design/Note.md", "x\n")
        git(self.tree, "push", "-q", "origin", PARENT)
        tree = self.start_child()
        git(tree, "mv", "wip/design/Note.md", "wip/design/note.md")
        git(tree, "commit", "-q", "-m", "rename")
        self.assert_unrestorable_stops("大文字小文字だけが違うパス")

    def test_adding_a_submodule_stops_without_merging(self):
        self.start_parent()
        tree = self.start_child()
        sha = self.sha(tree, "HEAD")
        git(tree, "update-index", "--add", "--cacheinfo", f"160000,{sha},wip/design/sub")
        git(tree, "commit", "-q", "-m", "submodule")
        # 初期化していない submodule と同じ形（空のディレクトリ）にして、未コミットに見せない
        os.makedirs(os.path.join(tree, "wip", "design", "sub"))
        self.assert_unrestorable_stops("submodule の追加・削除")


if __name__ == "__main__":
    unittest.main()
