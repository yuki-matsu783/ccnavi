"""途中のリンク・junction の判定（`infra/pathsafe`）の受入テスト。

同じ表（リンクなし・途中がリンク・最後の名前がリンク・root を指す子のリンク・root の外・
root 自身・存在しない・root 自身がリンク・大文字小文字違い・junction）を、判定を使う側の入口
（`learning.unsafe_path`・`archive._linked`）にも当てて、どこも同じ答えを返すことを確かめる。
junction は Linux では作れないので `os.path.isjunction` を差し替えて見る（Windows では本物も作る）。
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from ccnavi.infra import pathsafe
from ccnavi.tickets import learning
from ccnavi.tickets.model import archive


def _touch(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("x")


# (名前, root の種類, root からの相対, 道（最後を含む）にリンクがあるか, 最後を除いた道にあるか)
ROWS = (
    ("リンクなし", "root", "plain/sub/file.txt", False, False),
    ("途中のディレクトリがリンク", "root", "dirlink/file.txt", True, True),
    ("最後の名前がリンク", "root", "real/filelink", True, False),
    ("root を指す子のリンク", "root", "selfdir/plain", True, True),
    ("root の外を指すリンク", "root", "outer/x", True, True),
    ("root 自身", "root", "", False, False),
    ("存在しない", "root", "plain/nope/x", False, False),
    ("root 自身がリンク", "rootlink", "plain/sub/file.txt", False, False),
    ("大文字小文字違いの root", "upper", "plain/sub/file.txt", False, False),
    ("途中が junction", "root", "junc/inner/f", True, True),
)


class PathsafeTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = os.path.realpath(tmp.name)
        self.root = os.path.join(self.tmp, "root")
        _touch(os.path.join(self.root, "real", "file.txt"))
        _touch(os.path.join(self.root, "plain", "sub", "file.txt"))
        _touch(os.path.join(self.root, "junc", "inner", "f"))
        os.makedirs(os.path.join(self.tmp, "outside"))
        try:
            os.symlink(os.path.join(self.root, "real"), os.path.join(self.root, "dirlink"))
            os.symlink(os.path.join(self.tmp, "outside"), os.path.join(self.root, "outer"))
            os.symlink("file.txt", os.path.join(self.root, "real", "filelink"))
            os.symlink(self.root, os.path.join(self.root, "selfdir"))
            os.symlink(self.root, os.path.join(self.tmp, "rootlink"))
        except (OSError, NotImplementedError):
            self.skipTest("シンボリックリンクを作れない環境")
        self.junction = os.path.join(self.root, "junc")
        self.roots = {
            "root": self.root,
            "rootlink": os.path.join(self.tmp, "rootlink"),
            "upper": os.path.join(self.tmp, "ROOT"),
        }
        patcher = mock.patch.object(os.path, "isjunction", lambda p: p == self.junction)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _rows(self):
        for name, kind, rel, linked, mid in ROWS:
            parts = tuple(p for p in rel.split("/") if p)
            root = self.roots[kind]
            yield name, root, parts, os.path.join(root, *parts), linked, mid

    def test_has_link(self):
        for name, root, _parts, path, linked, _mid in self._rows():
            with self.subTest(name):
                self.assertEqual(pathsafe.has_link(root, path), linked)

    def test_first_link(self):
        for name, root, parts, _path, linked, mid in self._rows():
            with self.subTest(name):
                self.assertEqual(pathsafe.first_link(root, parts) is not None, linked)
                self.assertEqual(
                    pathsafe.first_link(root, parts, include_last=False) is not None, mid
                )

    def test_first_link_returns_the_link_path(self):
        found = pathsafe.first_link(self.root, ("real", "filelink"))
        self.assertEqual(found, os.path.join(self.root, "real", "filelink"))
        found = pathsafe.first_link(self.root, ("dirlink", "file.txt"))
        self.assertEqual(found, os.path.join(self.root, "dirlink"))

    def test_learning_unsafe_path(self):
        for name, root, parts, _path, linked, _mid in self._rows():
            with self.subTest(name):
                self.assertEqual(bool(learning.unsafe_path(root, "/".join(parts))), linked)

    def test_learning_unsafe_path_names_the_link(self):
        got = learning.unsafe_path(self.root, "dirlink/file.txt")
        self.assertEqual(got, os.path.join(self.root, "dirlink"))

    def test_archive_linked_keeps_three_values(self):
        for name, root, parts, _path, linked, _mid in self._rows():
            if not parts:
                continue
            with self.subTest(name):
                got = archive._linked(root, parts)
                if linked:
                    self.assertIs(got, True)
                elif os.path.lexists(os.path.join(root, *parts)):
                    self.assertIs(got, False)
                else:
                    self.assertIsNone(got)

    def test_a_spelling_of_root_that_lands_on_it_is_not_a_link(self):
        # /tmp と /private/tmp のように、root より上のリンクを通った綴り
        parent = os.path.join(self.tmp, "parent")
        root = os.path.join(parent, "ws")
        _touch(os.path.join(root, "a", "b"))
        os.symlink(parent, os.path.join(self.tmp, "viaparent"))
        via = os.path.join(self.tmp, "viaparent", "ws", "a", "b")
        self.assertFalse(pathsafe.has_link(root, via))

    def test_a_link_to_the_parent_that_comes_back_into_root_is_a_link(self):
        os.symlink(self.tmp, os.path.join(self.root, "up"))
        path = os.path.join(self.root, "up", "root", "real", "file.txt")
        self.assertTrue(pathsafe.has_link(self.root, path))
        self.assertTrue(pathsafe.first_link(self.root, ("up", "root", "real", "file.txt")))

    def test_a_path_outside_root_is_a_link(self):
        self.assertTrue(pathsafe.has_link(self.root, os.path.join(self.tmp, "outside", "x")))
        self.assertTrue(pathsafe.has_link(self.root, self.tmp))

    def test_a_failure_while_checking_is_a_link(self):
        plain = os.path.join(self.root, "plain")
        with mock.patch.object(os.path, "realpath", side_effect=OSError("boom")):
            self.assertTrue(pathsafe.has_link(self.root, plain))
            self.assertTrue(pathsafe.escapes(self.root, plain))
        outside = os.path.join(self.tmp, "outside", "x")
        with mock.patch.object(os.path, "realpath", side_effect=ValueError("nul")):
            self.assertTrue(pathsafe.has_link(self.root, outside))

    def test_escapes(self):
        self.assertFalse(pathsafe.escapes(self.root, os.path.join(self.root, "plain")))
        self.assertFalse(pathsafe.escapes(self.root, self.root))
        self.assertFalse(pathsafe.escapes(self.root, os.path.join(self.root, "dirlink")))
        self.assertTrue(pathsafe.escapes(self.root, os.path.join(self.root, "outer")))
        self.assertTrue(pathsafe.escapes(self.root, os.path.join(self.tmp, "outside")))
        self.assertFalse(pathsafe.escapes(self.roots["upper"], os.path.join(self.root, "plain")))

    def test_is_link(self):
        self.assertTrue(pathsafe.is_link(os.path.join(self.root, "dirlink")))
        self.assertTrue(pathsafe.is_link(self.junction))
        self.assertFalse(pathsafe.is_link(os.path.join(self.root, "plain")))
        self.assertFalse(pathsafe.is_link(os.path.join(self.root, "missing")))


@unittest.skipUnless(os.name == "nt", "本物の junction は Windows だけ")
class RealJunctionTest(unittest.TestCase):
    def test_a_real_junction_is_a_link(self):
        import _winapi

        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.realpath(tmp)
            target = os.path.join(root, "target")
            os.makedirs(os.path.join(target, "in"))
            junction = os.path.join(root, "junction")
            _winapi.CreateJunction(target, junction)
            self.assertTrue(pathsafe.is_link(junction))
            self.assertTrue(pathsafe.has_link(root, os.path.join(junction, "in")))
            self.assertIsNotNone(pathsafe.first_link(root, ("junction", "in")))
            self.assertFalse(pathsafe.has_link(root, os.path.join(target, "in")))


if __name__ == "__main__":
    unittest.main()
