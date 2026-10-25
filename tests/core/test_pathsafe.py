"""`infra/pathsafe` の入出力表。リンクの判定はここを通す。"""

from __future__ import annotations

import os
import tempfile
import unittest

from ccnavi.infra import pathsafe


def _symlink(target: str, link: str) -> bool:
    try:
        os.symlink(target, link, target_is_directory=os.path.isdir(target))
    except (OSError, NotImplementedError):
        return False
    return True


class PathsafeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = os.path.realpath(tmp.name)
        self.root = os.path.join(self.base, "root")
        self.out = os.path.join(self.base, "out")
        os.makedirs(os.path.join(self.root, "a", "b"))
        os.makedirs(self.out)
        with open(os.path.join(self.root, "a", "b", "f.txt"), "w") as f:
            f.write("x")

    def link(self, target: str, name: str) -> str:
        path = os.path.join(self.root, name)
        if not _symlink(target, path):
            self.skipTest("シンボリックリンクを作れない")
        return path

    def test_no_link(self):
        p = os.path.join(self.root, "a", "b", "f.txt")
        self.assertFalse(pathsafe.has_link(self.root, p))
        self.assertFalse(pathsafe.has_link(self.root, self.root))
        self.assertIsNone(pathsafe.first_link(self.root, ["a", "b", "f.txt"]))
        self.assertFalse(pathsafe.escapes(self.root, p))

    def test_middle_link(self):
        self.link(self.out, "m")
        p = os.path.join(self.root, "m", "x")
        self.assertTrue(pathsafe.has_link(self.root, p))
        self.assertEqual(pathsafe.first_link(self.root, ["m", "x"]), os.path.join(self.root, "m"))

    def test_last_link(self):
        self.link(self.out, "last")
        p = os.path.join(self.root, "last")
        self.assertTrue(pathsafe.has_link(self.root, p))
        self.assertIsNone(pathsafe.first_link(self.root, ["last"], include_last=False))
        self.assertTrue(pathsafe.first_link(self.root, ["last"]))

    def test_child_pointing_at_root(self):
        self.link(self.root, "loop")
        self.assertTrue(pathsafe.has_link(self.root, os.path.join(self.root, "loop")))

    def test_outside_root(self):
        self.assertTrue(pathsafe.has_link(self.root, self.out))
        self.assertTrue(pathsafe.escapes(self.root, self.out))

    def test_escapes_through_link(self):
        link = self.link(self.out, "m")
        self.assertTrue(pathsafe.escapes(self.root, link))

    def test_root_itself_link_is_not_counted(self):
        alias = os.path.join(self.base, "alias")
        if not _symlink(self.root, alias):
            self.skipTest("シンボリックリンクを作れない")
        self.assertFalse(pathsafe.has_link(alias, alias))
        self.assertFalse(pathsafe.has_link(alias, os.path.join(alias, "a")))

    def test_spelled_through_link_to_root_is_a_link(self):
        alias = os.path.join(self.base, "alias")
        if not _symlink(self.root, alias):
            self.skipTest("シンボリックリンクを作れない")
        self.assertTrue(pathsafe.has_link(self.root, os.path.join(alias, "a")))

    def test_missing(self):
        p = os.path.join(self.root, "nope", "x")
        self.assertFalse(pathsafe.has_link(self.root, p))
        self.assertEqual(pathsafe.scan(self.root, ["nope", "x"]), (pathsafe.MISSING, ""))
        self.assertEqual(pathsafe.scan(self.root, ["a", "b"]), (pathsafe.CLEAN, ""))

    def test_case_difference(self):
        # 大文字小文字だけ違う綴りは、どの機械でも同じ場所として数える（pathcase.fold）
        self.assertFalse(pathsafe.has_link(self.root, os.path.join(self.root.upper(), "a")))

    def test_fold_makes_spelling_equal(self):
        # 区別しない機械（Windows・macOS 既定）でも、Linux でも、root 自身の綴り違いは外に出ない
        self.assertFalse(pathsafe.escapes(self.root, self.root))

    def test_is_link(self):
        link = self.link(self.out, "m")
        self.assertTrue(pathsafe.is_link(link))
        self.assertFalse(pathsafe.is_link(os.path.join(self.root, "a")))
        self.assertFalse(pathsafe.is_link(os.path.join(self.root, "nope")))


if __name__ == "__main__":
    unittest.main()
