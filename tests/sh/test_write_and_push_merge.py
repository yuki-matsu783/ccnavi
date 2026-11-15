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
    """取り込み（merge）まわりの前半。

    合流の前の検査（置き場・範囲・親で無視されるファイル・戻せない形・子の未コミット）と衝突の文は
    Go の単体テスト（`go/internal/tickets/lifecycle/wapmerge/`・`phase/scope/`）が見る。ここでは、
    止まったときに sh が何も変えないことを、範囲の外・置き場の変更（1 本の通し）・戻せない形
    （改行を含むパス）・衝突で代表して見る。
    """

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
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        # 親子ごとの取り込み状態は作らない
        self.assertFalse(os.path.exists(os.path.join(self.state, "sync")))
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

    def test_a_path_outside_the_scope_or_a_ticket_place_stops_without_merging(self):
        # 子の準備は 1 回にして、子のコミットを差し替えて finish を 2 回回す。
        self.start_parent()
        tree = self.start_child()
        start = self.sha(tree, "HEAD")
        before = self.sha(self.tree, "HEAD")
        for rel, kind in (
            ("src/x.py", "範囲の外のパス"),
            ("wip/proposals/todo/evil.md", "置き場の変更"),
        ):
            with self.subTest(kind=kind):
                git(tree, "reset", "-q", "--hard", start)
                self.work(tree, rel, "x\n")
                result = self.ticket("finish", CHILD)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(kind, result.stderr)
                self.assertIn(rel, result.stderr)
                self.assert_untouched(before)

    def test_a_path_with_a_newline_stops_without_merging(self):
        self.start_parent()
        tree = self.start_child()
        self.work(tree, "wip/design/a\nb.md", "x\n")
        self.assert_unrestorable_stops("改行を含むパス")


if __name__ == "__main__":
    unittest.main()
