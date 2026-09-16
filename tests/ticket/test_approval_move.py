"""チケットを置き場から置き場へ動かす（approval.move_file）の受入テスト。

ADR-0055 で、承認済みチケットは 1 本のファイルとして doing/ → review/ → done/ と動く。
動かす途中で止まると同じ識別子が 2 か所に残り、以後どの操作も「複数の場所にある」で
止まる。見るのは 3 つ。

1. 同じファイルシステムの中では rename で動き、中身が変わらない
2. またぐとき（rename が通らない）は写して消す。消せなければ写した側を消して戻す
3. 行き先に同じ名前が既に在れば動かさず、どちらにも触らない
"""

from __future__ import annotations

import errno
import os
import tempfile
import unittest
from unittest import mock

from ccnavi import approval

TEXT = "---\nticket: i0001\n---\nbody\n"


class MoveFileTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = directory.name
        self.source = os.path.join(self.root, "doing", "i0001.md")
        self.target = os.path.join(self.root, "done", "i0001.md")
        os.makedirs(os.path.dirname(self.source))
        with open(self.source, "w", encoding="utf-8") as f:
            f.write(TEXT)

    def read(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_moves_by_rename_and_keeps_the_text(self):
        self.assertEqual(approval.move_file(self.source, self.target), "")
        self.assertFalse(os.path.exists(self.source))
        self.assertEqual(self.read(self.target), TEXT)

    def test_copies_and_removes_when_rename_is_refused(self):
        # 別のファイルシステムをまたぐ形。rename は EXDEV で通らない。
        with mock.patch("os.rename", side_effect=OSError(errno.EXDEV, "cross-device")):
            self.assertEqual(approval.move_file(self.source, self.target), "")
        self.assertFalse(os.path.exists(self.source))
        self.assertEqual(self.read(self.target), TEXT)

    def test_rolls_back_the_copy_when_the_source_cannot_be_removed(self):
        # 写せたが消せない（Windows で開かれている、権限が無い）。写した側を消して戻す。
        # 両方に残すと、以後どの操作も「複数の場所にある」で止まる。
        real_remove = os.remove

        def remove(path):
            # 消せないのは元のファイルだけ。巻き戻しで写した側を消す手は通す。
            if os.path.abspath(path) == os.path.abspath(self.source):
                raise PermissionError(errno.EACCES, "busy")
            real_remove(path)

        with (
            mock.patch("os.rename", side_effect=OSError(errno.EXDEV, "cross-device")),
            mock.patch("os.remove", side_effect=remove),
        ):
            failed = approval.move_file(self.source, self.target)
        self.assertIn("busy", failed)
        self.assertEqual(self.read(self.source), TEXT)
        self.assertFalse(os.path.exists(self.target))

    def test_refuses_when_the_target_already_exists(self):
        os.makedirs(os.path.dirname(self.target))
        with open(self.target, "w", encoding="utf-8") as f:
            f.write("older\n")
        failed = approval.move_file(self.source, self.target)
        self.assertIn("行き先に既に在る", failed)
        self.assertEqual(self.read(self.source), TEXT)
        self.assertEqual(self.read(self.target), "older\n")


if __name__ == "__main__":
    unittest.main()
