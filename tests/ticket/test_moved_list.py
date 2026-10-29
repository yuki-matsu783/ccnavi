"""依頼のあとに HEAD が動いて止めたときの説明（手元）。判定は変えない。

- 何が変わったかは断定しない固定の文: 依頼時のコミットといまの HEAD が違う。何が変わったかユーザに
  確かめてもらい、依頼し直す。コミットの一覧は出さない（依頼時のコミットが祖先でなくても同じ文）
- 依頼のあとに変えて戻した（revert で差分が空）なら、判定は今どおり通る
"""

from __future__ import annotations

import json
import os

from tests.ticket.test_review_actor import ActorHarness
from tests.ticket.test_ticket import git, write

FIXED = "依頼時のコミットといまの HEAD が違う。何が変わったかユーザに確かめてもらい、依頼し直す。"


class MovedListTest(ActorHarness):
    def commit_as(self, author, subject, files):
        for rel, text in files.items():
            write(os.path.join(self.parent_tree, *rel.split("/")), text)
        git(self.parent_tree, "add", "-A")
        git(
            self.parent_tree,
            "-c",
            f"user.name={author}",
            "-c",
            "user.email=a@example.invalid",
            "commit",
            "--quiet",
            "-m",
            subject,
        )

    def push(self):
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")

    def refused(self, fixture):
        done = self.confirm_as(fixture)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        return done.stderr

    def test_the_moved_head_gets_the_fixed_explanation_without_a_commit_list(self):
        fixture = self.ready()
        self.commit_as("alice", "最初の修正", {"src/a.py": "1\n"})
        self.push()
        said = self.refused(fixture)
        self.assertIn("依頼の後に親の HEAD が動いている", said)
        self.assertIn(FIXED, said)
        self.assertNotIn("最初の修正", said)
        self.assertNotIn("alice", said)
        self.assertIn("依頼を出し直してください", said)

    def test_a_recorded_commit_that_is_not_an_ancestor_gets_the_same_explanation(self):
        fixture = self.ready()
        self.commit_as("alice", "修正", {"src/a.py": "1\n"})
        self.push()
        # 依頼時のコミットが祖先でない（履歴を書き換えた形）でも、同じ固定の文
        orphan = git(
            self.parent_tree,
            "commit-tree",
            "-m",
            "別の履歴",
            "4b825dc642cb6eb9a060e54bf8d69288fbee4904",
        ).strip()
        mark = self.mark_path("requested")
        with open(mark, encoding="utf-8") as f:
            body = f.read()
        data = json.loads(body)
        data["head"] = orphan
        write(mark, json.dumps(data))
        said = self.refused(fixture)
        self.assertIn("依頼の後に親の HEAD が動いている", said)
        self.assertIn(FIXED, said)
        self.assertIn("依頼を出し直してください", said)

    def test_a_change_that_was_reverted_leaves_no_diff_and_the_judgement_passes(self):
        fixture = self.ready()
        self.commit_as("alice", "足す", {"src/tmp.py": "x\n"})
        git(self.parent_tree, "revert", "--no-edit", "HEAD")
        self.push()
        done = self.confirm_as(fixture)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("依頼の後に親の HEAD が動いている", done.stderr)

    def test_a_push_pending_stop_adds_no_moved_explanation(self):
        """動いたのではなく push されていないだけのとき（kind が空）は、説明を足さない。"""
        from ccnavi.tickets.review import request as review

        self.assertEqual(
            review.moved_explanation(review.Moved("親ブランチの HEAD が push されていない")), []
        )

    def test_decide_says_the_same_when_the_head_moved(self):
        fixture = self.ready()
        self.commit_as("alice", "修正", {"src/a.py": "1\n"})
        self.push()
        done = self.ccnavi(
            "--cwd",
            self.parent_tree,
            "user",
            "reviewed",
            "1",
            "--accept-unresolved",
            "--preview",
            "--json",
            "--result",
            fixture,
        )
        self.assertNotEqual(done.returncode, 0)
        self.assertIn(FIXED, done.stderr)
