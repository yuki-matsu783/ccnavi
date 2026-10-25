"""`infra/pathsafe` の入出力を確かめるテスト。リンクの判定は `pathsafe` を通す。"""

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
        # 大文字小文字だけが違う綴りも、どの機械でも同じ場所として扱う（pathcase.fold）
        self.assertFalse(pathsafe.has_link(self.root, os.path.join(self.root.upper(), "a")))

    def test_fold_makes_spelling_equal(self):
        # 大文字小文字を区別しない機械（Windows・macOS の既定）でも、Linux でも、
        # root 自身の綴りの違いは外に出たことにならない
        self.assertFalse(pathsafe.escapes(self.root, self.root))

    def test_is_link(self):
        link = self.link(self.out, "m")
        self.assertTrue(pathsafe.is_link(link))
        self.assertFalse(pathsafe.is_link(os.path.join(self.root, "a")))
        self.assertFalse(pathsafe.is_link(os.path.join(self.root, "nope")))

    def test_first_link_stops_at_missing(self):
        self.link(self.out, "m")
        self.assertIsNone(pathsafe.first_link(self.root, ["nope", "m"]))

    def test_junction_counts_as_link(self):
        from unittest import mock

        target = os.path.join(self.root, "a")
        with mock.patch("os.path.isjunction", lambda p: p == target):
            self.assertTrue(pathsafe.is_link(target))
            self.assertTrue(pathsafe.has_link(self.root, os.path.join(target, "b")))
            self.assertEqual(pathsafe.scan(self.root, ["a", "b"]), (pathsafe.LINK, target))

    def test_callers_keep_their_meaning(self):
        from ccnavi.records import prune
        from ccnavi.tickets import archive, learning, review

        link = self.link(self.out, "m")
        self.assertEqual(learning.unsafe_path(self.root, "m/x.md"), link)
        self.assertEqual(learning.unsafe_path(self.root, "a/b/f.txt"), "")
        self.assertTrue(review._escapes(self.root, "m/x"))
        self.assertFalse(review._escapes(self.root, "a/b/f.txt"))
        # 最後の名前がリンクなのは構わない
        self.assertFalse(review._escapes(self.root, "m"))
        self.assertFalse(prune._linkless(self.root, link))
        self.assertTrue(prune._linkless(self.root, os.path.join(self.root, "a", "b")))
        self.assertFalse(prune._linkless(self.root, self.out))
        self.assertIs(archive._linked(self.root, ("m",)), True)
        self.assertIs(archive._linked(self.root, ("a", "b")), False)
        self.assertIsNone(archive._linked(self.root, ("a", "nope")))

    def test_prune_keeps_outside_by_spelling(self):
        from ccnavi.records import prune

        alias = os.path.join(self.base, "alias")
        if not _symlink(self.root, alias):
            self.skipTest("シンボリックリンクを作れない")
        # root が別綴り（リンク経由）で、path が実体の表記のときは、今までどおり範囲外で何も消さない
        self.assertFalse(prune._linkless(alias, os.path.join(self.root, "a")))
        self.assertTrue(prune._linkless(alias, os.path.join(alias, "a")))
        self.assertFalse(prune._linkless("relative-root", self.root))

    def test_has_link_lands_when_root_spelled_differently(self):
        # path が root と違う綴り（root より上のリンク経由）でも、実体が root に着けば止まる
        up = os.path.join(self.base, "up")
        if not _symlink(self.base, up):
            self.skipTest("シンボリックリンクを作れない")
        self.assertFalse(pathsafe.has_link(self.root, os.path.join(up, "root", "a", "b")))


if __name__ == "__main__":
    unittest.main()
