"""改行が CRLF のチケットを読めることを、明示に確かめる。

ここは今まで**偶然**通っていた。Windows で `core.autocrlf = true` にしている人の機械では、
テストがワークツリーに取り出すチケットが CRLF になり、そのまま読まれていた。設定は
走った機械のもので、テストが頼んだものではない。

`tests/__init__.py` が走った機械の git の設定を締め出したので、その偶然は消えた。
偶然に頼っていた被覆は、頼るのをやめるときに明示へ移す。移さずに締め出すと、
被覆が減ったことが誰にも見えない。

CRLF が届く道は Windows の checkout だけではない。CRLF で書く編集機、CRLF のまま
貼り付けられた本文、Windows で作って送られてきたチケットのどれでも届く。だから
git を通さずに、読み手に直に CRLF を渡して見る。git の変換の挙動ではなく、
ccnavi がその綴りを読めるかがここの主題。
"""

import os
import shutil
import tempfile
import unittest

from ccnavi import ticket

TICKET = """---
version: 1
ticket: i0001
human_review:
  required: false
  reason: テスト
title: 作業
rationale: |
  理由
allow:
  - match: Write|Edit
    glob: "src/*"
started_at: ""
completed_at: ""
base_sha: ""
---

本文の 1 行目
本文の 2 行目
"""


class CrlfTicketTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ccnavi-crlf-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def write(self, newline):
        """改行を指定してチケットを 1 本置く。`newline=""` は書いたままを通す。"""
        path = os.path.join(self.dir, "i0001.md")
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(TICKET.replace("\n", newline))
        return path

    def test_crlf_is_read_the_same_as_lf(self):
        """CRLF でも LF でも、読めた中身が一致する。

        「読める」だけでは足りない。欄が 1 つでも違う読まれ方をすると、範囲の判定が
        機械によって変わる。
        """
        lf, _ = ticket.load(self.write("\n"))
        crlf, _ = ticket.load(self.write("\r\n"))
        self.assertIsNotNone(lf)
        self.assertIsNotNone(crlf, "CRLF のチケットが読めない")
        self.assertEqual(lf.raw, crlf.raw)
        self.assertEqual(lf.ticket, crlf.ticket)
        self.assertEqual(lf.title, crlf.title)

    def test_the_body_keeps_no_carriage_return(self):
        """本文に `\\r` が残らない。

        残ると、本文を行で突き合わせる側（レビューの指摘、引き継ぎの下書き）が
        末尾の見えない 1 文字で食い違う。
        """
        crlf, _ = ticket.load(self.write("\r\n"))
        self.assertIsNotNone(crlf)
        self.assertNotIn("\r", crlf.body)

    def test_the_scope_is_the_same(self):
        """範囲の欄が同じに読まれる。ここが違うと、止まる場所が機械で変わる。"""
        lf, _ = ticket.load(self.write("\n"))
        crlf, _ = ticket.load(self.write("\r\n"))
        self.assertEqual(lf.raw.get("allow"), crlf.raw.get("allow"))


if __name__ == "__main__":
    unittest.main()
