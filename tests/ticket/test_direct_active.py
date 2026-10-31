"""SessionStart で、作業中の直接作業のワークツリーを知らせる（direct_active）のテスト。

compact をまたいで会話が忘れた続きの作業を、別のワークツリーで切り直さないために、記録
（`ccnavi-direct/<名前>`）と作業ワークツリーが残っているものを名前とパスで知らせる。
"""

from __future__ import annotations

import json
import os
import unittest

from ccnavi.infra import settings
from ccnavi.tickets.notice import direct_active
from tests.ticket.test_ticket import TicketTest, write


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for name in sorted(vars(DirectActiveTest)):
        if name.startswith("test_"):
            suite.addTest(DirectActiveTest(name))
    return suite


class DirectActiveTest(TicketTest):
    def record(self, name, version="4", make_dir=True):
        path = os.path.join(self.root, ".git", "ccnavi-direct", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write(path, f"version {version}\nbase_branch main\n")
        if make_dir:
            os.makedirs(os.path.join(self.root, ".claude", "worktrees", name), exist_ok=True)

    def notice(self):
        return direct_active.notice(settings.Settings(projects=""), self.root)

    def test_no_record_says_nothing(self):
        self.assertEqual(self.notice(), "")

    def test_a_record_with_its_worktree_is_named_with_the_path(self):
        self.record("chore-1-data-schema")
        text = self.notice()
        self.assertIn("chore-1-data-schema", text)
        self.assertIn(os.path.join(".claude", "worktrees", "chore-1-data-schema"), text)

    def test_every_active_worktree_is_named(self):
        self.record("chore-1-a")
        self.record("chore-2-b")
        text = self.notice()
        self.assertIn("chore-1-a", text)
        self.assertIn("chore-2-b", text)

    def test_a_record_without_its_worktree_is_not_active(self):
        self.record("gone", make_dir=False)
        self.assertEqual(self.notice(), "")

    def test_a_record_of_another_version_is_not_active(self):
        self.record("old", version="3")
        self.assertEqual(self.notice(), "")

    def test_it_is_told_again_after_a_compact(self):
        self.record("chore-1-data-schema")
        payload = {
            "hook_event_name": "SessionStart",
            "source": "compact",
            "session_id": "ss",
            "cwd": self.root,
        }
        out = self.ccnavi("--mode", "enable", stdin=json.dumps(payload)).stdout
        self.assertIn("chore-1-data-schema", out)


if __name__ == "__main__":
    unittest.main()
