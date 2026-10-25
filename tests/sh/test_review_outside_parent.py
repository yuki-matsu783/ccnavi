"""ccnavi-review.sh を親のワークツリーの外（ワークスペースルート）で打ったときの案内。

どの親かは cwd から引く。ワークスペースルートのブランチは統合先（main）で、親ではない。
そこで打たれたら、親のワークツリーを切り直させず、親のワークツリーの中で打たせる。
`--parent` は ready だけが受ける。ほかのサブコマンドでは黙って無視せず、終了コード 2 で断る。
"""

from __future__ import annotations

import unittest

from tests.sh.write_and_push_harness import PARENT, WriteAndPushHostHarness


class OutsideTheParentTreeTest(WriteAndPushHostHarness):
    def review_at_root(self, *args):
        return self.sh("ccnavi-review.sh", *args, cwd=self.ws, PATH=self.path, GITLAB_TOKEN="t0k")

    def test_confirm_at_the_workspace_root_says_to_type_it_inside_the_parent_tree(self):
        done = self.review_at_root("confirm", "--phase", "1")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("親のワークツリー", done.stderr)
        self.assertIn("の中で打ってください", done.stderr)
        self.assertIn(f".claude/worktrees/{PARENT}", done.stderr)
        self.assertNotIn("切り直", done.stderr)
        self.assertNotIn("worktree add", done.stderr)

    def test_confirm_inside_the_parent_tree_is_not_stopped_by_that_check(self):
        done = self.review("confirm", "--phase", "1")
        self.assertNotIn("の中で打ってください", done.stderr)

    def test_parent_is_refused_for_every_subcommand_but_ready(self):
        for sub, extra in (
            ("confirm", ["--phase", "1"]),
            ("request", ["--phase", "1"]),
            ("comment", []),
            ("fetch", []),
        ):
            for form in (["--parent", PARENT], [f"--parent={PARENT}"]):
                with self.subTest(sub=sub, form=form):
                    done = self.review_at_root(sub, *extra, *form)
                    self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
                    self.assertIn("--parent は ready だけです", done.stderr)
                    self.assertIn("親のワークツリーの中で打ってください", done.stderr)

    def test_the_usage_line_lists_parent_for_ready_only(self):
        done = self.sh("ccnavi-review.sh", "--help")
        first = done.stdout.splitlines()[0]
        self.assertNotIn("--parent", first)
        ready = [x for x in done.stdout.splitlines() if x.lstrip().startswith("ready")]
        self.assertIn("--parent", ready[0])


if __name__ == "__main__":
    unittest.main()
