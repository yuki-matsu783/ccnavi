"""ツリーの判定は、どの機械でも大文字小文字を区別しない（#423 の続き）。

Windows で clone したときだけ失敗しないよう、Linux でも同じ判定になることを固定する。
"""

from __future__ import annotations

import os
import tempfile
import unittest

from ccnavi.infra import tree


class TreeCaseTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = os.path.realpath(tmp.name)
        self.main = tree.main_tree(self.root)

    def test_tree_of_は表記違いのパスでも同じツリーを返す(self) -> None:
        swapped = self.root.swapcase()
        found = tree.tree_of(self.root, os.path.join(swapped, "src", "a.py"))
        self.assertIsNotNone(found)
        self.assertEqual(found.root, self.main.root)

    def test_relative_は表記違いでも元の表記で相対を返す(self) -> None:
        swapped = self.root.swapcase()
        full = os.path.join(swapped, "README.md")
        self.assertEqual(tree.relative(self.main, full), "README.md")
        self.assertEqual(tree.relative(self.main, swapped), "")

    def test_lookup_は表記違いを同じ名前として引く(self) -> None:
        index = {"I0001-01-01": "a", "i0002-01-01": "b"}
        self.assertEqual(tree.lookup(index, "I0001-01-01"), "a")
        self.assertEqual(tree.lookup(index, "i0001-01-01"), "a")
        self.assertEqual(tree.lookup(index, "I0002-01-01"), "b")
        self.assertIsNone(tree.lookup(index, "i0003-01-01"))

    def test_worktree_path_は在るディレクトリの表記で返す(self) -> None:
        base = os.path.join(self.root, tree.WORKTREES_DIR)
        os.makedirs(os.path.join(base, "I0001-01-01"))
        self.assertEqual(
            tree.worktree_path(self.root, "i0001-01-01"), os.path.join(base, "I0001-01-01")
        )
        self.assertEqual(tree.worktree_path(self.root, "i0002"), os.path.join(base, "i0002"))

    def test_lookup_は綴りまで同じキーを先に返す(self) -> None:
        index = {"I0001-01-01": "A", "i0001-01-01": "B"}
        self.assertEqual(tree.lookup(index, "i0001-01-01"), "B")

    def test_parts_under_はルートが_の時も使える(self) -> None:
        # parts_under は実体化してから分ける（infra/tree.py の _resolved）。macOS の /etc は
        # /private/etc へのリンクなので、無いパスを使い、ルート直下の分割だけを見る。
        name = "ccnavi-no-such-top-level-dir"
        path = os.path.join(os.sep, name, "x")
        self.assertFalse(os.path.lexists(os.path.join(os.sep, name)))
        self.assertEqual(tree.parts_under(os.sep, path), [name, "x"])

    def test_owner_は大文字を含むルートでも所有ツリーを返す(self) -> None:
        from ccnavi.tickets import core_base

        upper = os.path.join(self.root, "Work")
        os.makedirs(upper)
        main = tree.main_tree(upper)
        owner = core_base._owner([main], os.path.join(upper, "a.md"))
        self.assertIsNotNone(owner)
        self.assertEqual(owner.root, main.root)


if __name__ == "__main__":
    unittest.main()
