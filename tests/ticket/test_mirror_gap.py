"""ミラーのずれ（共通レイヤーのコピー・`skills/`）の案内が、`finish` と `confirm` でも `ready` と
そろうこと（#707）。判定は変えず、案内の文だけを見る。

1. `finish`（`write_and_push_merge._tree_problem`）: プロジェクトの親だけミラーの案内。
   ワークスペース自身の親は従来のまま。混在のときはずれ以外の件数も出す
2. `confirm`（`review.moved_detail` と `moved_explanation`）: 動かしたのがミラー側だけか、一部か
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest

from ccnavi.infra import settings
from ccnavi.tickets.lifecycle import write_and_push_merge
from ccnavi.tickets.review import request as review

RECORDED = "a" * 40
HEAD = "b" * 40
MIRROR_FILE = ".ccnavi/config/common/rules.yml"
SKILL_FILE = "skills/process-test/SKILL.md"


def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


class TreeProblemTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tree = os.path.join(self._tmp.name, "parent")
        os.makedirs(self.tree)
        _git(self.tree, "init", "-q")
        self.conf = settings.Settings()

    def dirty(self, *rels):
        for rel in rels:
            path = os.path.join(self.tree, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write("old\n")
        _git(self.tree, "add", "-A")
        _git(self.tree, "commit", "-q", "-m", "x")
        for rel in rels:
            with open(os.path.join(self.tree, *rel.split("/")), "w", encoding="utf-8") as f:
                f.write("new\n")

    def problem(self, project):
        return write_and_push_merge._tree_problem(self.conf, self.tree, "", "i0001-01-01", project)

    def test_a_project_parent_gets_the_mirror_guidance(self):
        self.dirty(MIRROR_FILE, SKILL_FILE)
        why, hints = self.problem(True)
        self.assertIn(MIRROR_FILE, why)
        self.assertIn("コミットも取り消しもしない", why)
        self.assertNotIn("サブエージェントの作業がコミットされていません", why)
        self.assertNotIn("ずれ以外", why)
        self.assertEqual(len(hints), 1)
        self.assertIn("ccnavi-user-mirror.sh", hints[0])
        self.assertNotIn("restore", hints[0])

    def test_the_workspace_own_parent_keeps_the_old_guidance(self):
        self.dirty(MIRROR_FILE)
        why, hints = self.problem(False)
        self.assertIn("サブエージェントの作業がコミットされていません", why)
        self.assertNotIn("ccnavi-user-mirror.sh", why + "".join(hints))
        self.assertIn("restore", hints[0])

    def test_a_normal_change_in_a_project_parent_gets_no_mirror_guidance(self):
        self.dirty("src/keep.py")
        why, hints = self.problem(True)
        self.assertIn("サブエージェントの作業がコミットされていません", why)
        self.assertNotIn("ccnavi-user-mirror.sh", why + "".join(hints))

    def test_mixed_changes_name_the_count_of_the_other_changes(self):
        self.dirty(MIRROR_FILE, "src/keep.py")
        why, hints = self.problem(True)
        self.assertIn("ずれ以外の変更が 1 件", why)
        self.assertIn("src/keep.py", why)
        self.assertTrue(any("restore" in h for h in hints))
        self.assertTrue(any("ccnavi-user-mirror.sh" in h for h in hints))


class MovedTest(unittest.TestCase):
    def setUp(self):
        self.conf = settings.Settings()

    def explain(self, changed, project=True):
        moved = review.moved_detail(self.conf, {"head": RECORDED}, HEAD, changed, True, project)
        self.assertTrue(moved.text)
        return "\n".join(review.moved_explanation(moved))

    def test_only_mirror_paths_say_it_is_the_mirror_commit_and_a_new_request(self):
        text = self.explain([MIRROR_FILE, SKILL_FILE])
        self.assertIn("ミラーのずれ", text)
        self.assertIn("再 `request`", text)

    def test_a_mix_counts_the_mirror_side(self):
        text = self.explain([MIRROR_FILE, "src/keep.py"])
        self.assertIn("ミラー側", text)
        self.assertIn("1 件（全体 2 件）", text)
        self.assertNotIn("再 `request`", text)

    def test_normal_paths_get_no_mirror_sentence(self):
        self.assertNotIn("ミラー", self.explain(["src/keep.py"]))

    def test_the_workspace_own_parent_gets_no_mirror_sentence(self):
        self.assertNotIn("ミラー", self.explain([MIRROR_FILE], project=False))

    def test_unverified_gets_no_mirror_sentence(self):
        moved = review.moved_detail(self.conf, {"head": RECORDED}, HEAD, None)
        self.assertNotIn("ミラー", "\n".join(review.moved_explanation(moved)))


if __name__ == "__main__":
    unittest.main()
