"""ファイルのパスを当てる先に直す部分のテスト。

守る対象は名前ではなく場所なので、同じ場所を指す別の綴りが同じ判定に
行き着かなければならない。
"""

from __future__ import annotations

import os
import tempfile
import unittest

from ccnavi.cli import full_path


class FullPathTest(unittest.TestCase):
    def test_相対パスは呼び出し側の作業ディレクトリから決まる(self):
        with tempfile.TemporaryDirectory() as base:
            base = os.path.realpath(base)
            self.assertEqual(
                full_path("secrets/key.pem", base),
                os.path.join(base, "secrets", "key.pem"),
            )

    def test_上に戻る綴りは畳まれる(self):
        # `..` を挟めば、secrets を通らない綴りで secrets の中に届く。
        with tempfile.TemporaryDirectory() as base:
            base = os.path.realpath(base)
            self.assertEqual(
                full_path("build/../secrets/key.pem", base),
                os.path.join(base, "secrets", "key.pem"),
            )

    def test_同じ場所を指す綴りは同じ答えになる(self):
        with tempfile.TemporaryDirectory() as base:
            base = os.path.realpath(base)
            spellings = [
                "secrets/key.pem",
                "./secrets/key.pem",
                "secrets/./key.pem",
                "a/../secrets/key.pem",
                os.path.join(base, "secrets", "key.pem"),
            ]
            answers = {full_path(s, base) for s in spellings}
            self.assertEqual(len(answers), 1, f"綴りごとに違う答えになった: {answers}")

    def test_まだ無いファイルでも絶対パスになる(self):
        # 書き込みは、まだ存在しない先に向かうほうが普通。
        with tempfile.TemporaryDirectory() as base:
            got = full_path("does/not/exist/yet.env", base)
            self.assertTrue(os.path.isabs(got), got)
            self.assertIn("yet.env", got)

    def test_空のパスは空のまま(self):
        # 対象が無い呼び出しは判定せずに記録される。そこへ作業ディレクトリを
        # 返してしまうと、対象の無い呼び出しがディレクトリへの操作に見える。
        self.assertEqual(full_path("", "/somewhere"), "")


class RuleReachTest(unittest.TestCase):
    """正規化したパスに、ルールが実際に届くかどうか。"""

    def test_迂回した綴りでも保護領域のルールに当たる(self):
        import re

        from ccnavi.globmatch import translate

        pattern = re.compile(translate("*/secrets/*"))
        with tempfile.TemporaryDirectory() as base:
            base = os.path.realpath(base)
            sneaky = full_path("docs/../secrets/key.pem", base)
            self.assertIsNotNone(
                pattern.match(sneaky), f"迂回した綴りがルールをすり抜けた: {sneaky}"
            )


if __name__ == "__main__":
    unittest.main()
