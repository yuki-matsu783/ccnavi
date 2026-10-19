"""古い取り込み状態（`tickets/legacy_sync.py`）の読み取りと削除。

`ccnavi sync migrate` だけが使う。リンクは辿らず、読まない・消さない。
"""

from __future__ import annotations

import os
import tempfile
import unittest

from ccnavi.tickets import legacy_sync
from tests import requires_symlink
from tests.ticket.test_standing import record_text
from tests.ticket.test_ticket import write


class LegacySyncTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = self._tmp.name

    def put(self, rel, text):
        return write(os.path.join(self.state, *rel.split("/")), text)

    def exists(self, rel):
        return os.path.lexists(os.path.join(self.state, *rel.split("/")))

    def test_no_sync_dir_reads_nothing(self):
        self.assertEqual([], legacy_sync.read_families(self.state))
        self.assertEqual([], legacy_sync.read_families(""))
        self.assertEqual([], legacy_sync.integration_repos(self.state))

    def test_families_are_listed_with_branch_and_mr(self):
        self.put("sync/self/families/i0001", record_text("claude/i0001", "gone") + "mr 12\n")
        self.put("sync/web/families/w0002", record_text("w0002", "present") + "mr x\n")
        self.put("sync/self/families/i0003.tmp.9", "writing\n")
        found = legacy_sync.read_families(self.state)
        self.assertEqual(
            [("self", "i0001", "claude/i0001", "12"), ("web", "w0002", "w0002", "")],
            [(f.repo, f.name, f.branch, f.mr) for f in found],
        )
        self.assertEqual(["", ""], [f.skipped for f in found])

    @requires_symlink
    def test_a_linked_record_is_skipped_and_not_removed(self):
        real = self.put("elsewhere/i0001", record_text("i0001", "present"))
        os.makedirs(os.path.join(self.state, "sync", "self", "families"))
        os.symlink(real, os.path.join(self.state, "sync", "self", "families", "i0001"))
        found = legacy_sync.read_families(self.state)
        self.assertEqual(1, len(found))
        self.assertIn("シンボリックリンク", found[0].skipped)
        self.assertFalse(legacy_sync.remove_family(self.state, "self", "i0001"))
        self.assertTrue(
            os.path.islink(os.path.join(self.state, "sync", "self", "families", "i0001"))
        )
        self.assertTrue(os.path.isfile(real))

    @requires_symlink
    def test_a_linked_directory_on_the_way_is_not_followed(self):
        self.put("other/families/i0002", record_text("i0002", "present"))
        os.makedirs(os.path.join(self.state, "sync"))
        os.symlink(os.path.join(self.state, "other"), os.path.join(self.state, "sync", "p"))
        found = legacy_sync.read_families(self.state)
        self.assertEqual(1, len(found))
        self.assertIn("シンボリックリンク", found[0].skipped)
        self.assertFalse(legacy_sync.remove_family(self.state, "p", "i0002"))
        self.assertEqual(
            (False, "途中にシンボリックリンクがある"),
            legacy_sync.remove_integration(self.state, "p"),
        )

    def test_a_record_that_is_too_large_is_skipped(self):
        self.put("sync/self/families/i0001", "branch x\n" + "x" * (64 * 1024))
        (found,) = legacy_sync.read_families(self.state)
        self.assertIn("大きすぎる", found.skipped)

    def test_remove_family_prunes_empty_directories(self):
        self.put("sync/self/families/i0001", record_text("i0001", "present"))
        self.put("sync/self/families/i0002", record_text("i0002", "present"))
        self.assertTrue(legacy_sync.remove_family(self.state, "self", "i0001"))
        self.assertTrue(self.exists("sync/self/families"))
        self.assertTrue(legacy_sync.remove_family(self.state, "self", "i0002"))
        self.assertFalse(self.exists("sync"))
        self.assertFalse(legacy_sync.remove_family(self.state, "self", "i0002"))

    def test_the_integration_copy_is_found_and_removed(self):
        self.put("sync/self/integration/head", "branch main\n")
        self.put("sync/self/integration/.ccnavi/approved/done/x.md", "x\n")
        self.put("sync/self/integration.tmp.9/head", "branch main\n")
        self.put("sync/web/families/w0002", record_text("w0002", "present"))
        self.assertEqual(["self"], legacy_sync.integration_repos(self.state))
        self.assertEqual((True, ""), legacy_sync.remove_integration(self.state, "self"))
        self.assertFalse(self.exists("sync/self"))
        self.assertTrue(self.exists("sync/web/families/w0002"))
        self.assertEqual((False, ""), legacy_sync.remove_integration(self.state, "self"))
        self.assertEqual([], legacy_sync.integration_repos(self.state))

    @requires_symlink
    def test_a_link_inside_the_integration_copy_is_removed_as_a_link(self):
        outside = self.put("secret.md", "s\n")
        self.put("sync/self/integration/head", "branch main\n")
        os.symlink(outside, os.path.join(self.state, "sync", "self", "integration", "l.md"))
        self.assertEqual((True, ""), legacy_sync.remove_integration(self.state, "self"))
        self.assertTrue(os.path.isfile(outside))


if __name__ == "__main__":
    unittest.main()
