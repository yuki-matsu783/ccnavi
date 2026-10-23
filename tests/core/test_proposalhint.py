"""親の提案を手書きしたときの書式の案内（PostToolUse）。

見るのは 4 つ。

1. 親のつもりで `parent:` を書いた提案（識別子が子の形でない）に、`parent:` と `phase:` を
   消す案内を出す
2. `parent:` の無い親に `mr:`・`plan:`・`phases:` が欠けていれば、欠けた欄を名指しする
3. 子の提案と、欄が揃った親には出さない
4. 新しく作った提案の `todo/` の直下以外・Edit・md でないものには出さない
"""

from __future__ import annotations

import os
import tempfile
import unittest

from ccnavi.hook import proposalhint
from ccnavi.infra import hookio, settings

PARENT_WRONG = "---\nversion: 1\nticket: feature-1-memo\nparent: feature-1-memo\nphase: 0\n---\n"
PARENT_BARE = "---\nversion: 1\nticket: feature-1-memo\ntitle: t\nmr: 3\n---\n"
PARENT_FULL = (
    "---\nversion: 1\nticket: feature-1-memo\nissue: 1\nmr: 3\nplan: [a]\nphases: {a: {}}\n---\n"
)
CHILD = "---\nversion: 1\nticket: feature-1-memo-01-01\nparent: feature-1-memo\nphase: 1\n---\n"


class ProposalHintTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = os.path.realpath(tmp.name)
        os.makedirs(os.path.join(self.root, ".ccnavi", "scripts"))
        with open(os.path.join(self.root, ".ccnavi", "scripts", "ccnavi-common.sh"), "w") as f:
            f.write("")

    def hint(self, rel, text, tool="Write", kind="create"):
        path = os.path.join(self.root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        payload = hookio.Input(
            event=hookio.POST_TOOL_USE,
            tool_name=tool,
            tool_input={"file_path": path},
            cwd=self.root,
            tool_response={"type": kind, "filePath": path},
        )
        return proposalhint.hint(settings.load(self.root)[0], self.root, payload)

    def test_a_parent_written_with_parent_key_is_told_to_drop_it(self):
        got = self.hint("wip/proposals/todo/feature-1-memo.md", PARENT_WRONG)
        self.assertIn(proposalhint.CODE_PARENT_KEY, got)
        self.assertIn("`parent:` と `phase:` の行を消して", got)
        self.assertIn("ccnavi ticket new", got)

    def test_a_parent_missing_fields_is_named(self):
        got = self.hint("wip/proposals/todo/feature-1-memo.md", PARENT_BARE)
        self.assertIn(proposalhint.CODE_PARENT_FIELDS, got)
        self.assertIn("`plan:`・`phases:`", got)
        self.assertNotIn("`mr:`", got)

    def test_a_missing_issue_is_told(self):
        text = PARENT_FULL.replace("issue: 1\n", "")
        got = self.hint("wip/proposals/todo/feature-1-memo.md", text)
        self.assertIn("`issue:`", got)

    def test_an_empty_plan_counts_as_missing(self):
        text = PARENT_FULL.replace("plan: [a]", "plan: []")
        got = self.hint("wip/proposals/todo/feature-1-memo.md", text)
        self.assertIn("`plan:`", got)

    def test_a_complete_parent_and_a_child_get_nothing(self):
        self.assertEqual("", self.hint("wip/proposals/todo/feature-1-memo.md", PARENT_FULL))
        self.assertEqual("", self.hint("wip/proposals/todo/feature-1-memo-01-01.md", CHILD))

    def test_a_proposal_in_a_misplaced_worktree_inside_the_tree_is_still_told(self):
        # ログで起きた形: ワークツリーがプロジェクトの中の `.claude/worktrees/` にできた
        rel = ".claude/worktrees/feature-1-memo/wip/proposals/todo/feature-1-memo.md"
        self.assertIn(proposalhint.CODE_PARENT_KEY, self.hint(rel, PARENT_WRONG))

    def test_a_child_pointing_at_another_parent_is_left_to_the_loader(self):
        text = CHILD.replace("parent: feature-1-memo", "parent: feature-2-other")
        self.assertEqual("", self.hint("wip/proposals/todo/feature-1-memo-01-01.md", text))

    def test_other_places_and_edits_get_nothing(self):
        todo = "wip/proposals/todo/feature-1-memo.md"
        self.assertEqual("", self.hint(todo, PARENT_WRONG, kind="update"))
        self.assertEqual("", self.hint(todo, PARENT_WRONG, tool="Edit"))
        self.assertEqual("", self.hint("wip/proposals/review/feature-1-memo.md", PARENT_WRONG))
        self.assertEqual("", self.hint("docs/feature-1-memo.md", PARENT_WRONG))


if __name__ == "__main__":
    unittest.main()
