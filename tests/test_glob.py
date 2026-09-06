"""glob の翻訳。意味は `fnmatch` そのままで、足しているのは区切りの正規化だけ。

見るのは 3 つ。文字列全体に当たること、区切り文字がどちらの綴りでも当たること、
そして語の切れ目が入らないこと。3 つ目は失われた機能ではなく決めた仕様で、
ここに書いておかないと、次に触る人が「入れ忘れ」と読んで戻してしまう。
"""

from __future__ import annotations

import re
import unittest

from ccnavi.globmatch import translate


class TranslateTest(unittest.TestCase):
    def hit(self, glob: str, subject: str) -> bool:
        return re.match(translate(glob), subject) is not None

    def test_文字列全体に当たる(self):
        # 部分一致は自動で足さない。足すと「書いたものがそのまま当たる」が
        # 崩れて、やめたはずの見えない層が戻ってくる。
        self.assertTrue(self.hit("git push", "git push"))
        self.assertFalse(self.hit("git push", "cd /repo && git push"))
        self.assertTrue(self.hit("*git push*", "cd /repo && git push"))

    def test_区切り文字はどちらの綴りにも当たる(self):
        # ルールは 1 回書いて、どの機械でも同じ意味でなければならない。
        for subject in ("/repo/secrets/token.txt", "C:\\repo\\secrets\\token.txt"):
            with self.subTest(subject=subject):
                self.assertTrue(self.hit("*/secrets/*", subject))
        self.assertFalse(self.hit("*/secrets/*", "/repo/docs/readme.md"))

    def test_語の切れ目は入らない(self):
        # 入らないことが仕様。右側だけなら空白を書いて守れる。
        self.assertTrue(self.hit("*sed*", "sedate --now"))
        self.assertFalse(self.hit("*sed *", "sedate --now"))
        self.assertTrue(self.hit("*sed *", "cd /repo && sed -i s/a/b/ x"))
        # 左側は glob では書けない。regex を使うことになる。
        self.assertTrue(self.hit("*git push*", "legit push"))

    def test_ワイルドカードは語の中でも当たる(self):
        # 自前の翻訳が語の切れ目を勝手に挟んでいた頃は、ここが当たらなかった。
        for glob, subject in (
            ("*foo*bar*", "foobar"),
            ("*foo*bar*", "fooXbar"),
            ("*id_*sa*", "id_rsa"),
            ("*id_?sa*", "id_rsa"),
        ):
            with self.subTest(glob=glob, subject=subject):
                self.assertTrue(self.hit(glob, subject))

    def test_いま使っている書き方が意図どおり当たる(self):
        # ルールファイルと組み込みの既定にある書き方を、実例で押さえる。
        # 翻訳後の式ではなく当たり方で書くのは、fnmatch の出す式が
        # 版によって変わりうるため。守りたいのは式ではなく当たり方。
        for glob, subject, want in (
            ("*git push*", "git push origin main", True),
            ("*git push*", "cd /repo && git push", True),
            ("*git push*", "git status", False),
            ("*rm -rf *", "rm -rf /tmp/x", True),
            ("*rm -rf *", "rm -r /tmp/x", False),
            ("*/.claude/ccnavi/*", "/repo/.claude/ccnavi/rules.yml", True),
            ("*/.claude/ccnavi/*", "/repo/.claude/hooks/lint.sh", False),
            ("*/.current-ticket.md", "/repo/.current-ticket.md", True),
            ("*.env*", "/repo/.env.local", True),
            ("*", "なんでも", True),
        ):
            with self.subTest(glob=glob, subject=subject):
                self.assertEqual(self.hit(glob, subject), want)


if __name__ == "__main__":
    unittest.main()
