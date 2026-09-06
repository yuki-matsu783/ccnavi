"""やさしいパターン記法の翻訳。

見るのは 2 つ。ワイルドカードが語の中でも当たること、そして語の切れ目が
ワイルドカード以外のところでは今までどおり効いていること。

2 つ目のためにルールで実際に使っている書き方を並べてある。この記法は
「書いた人が当たるつもりのものに当たる」ことが取り柄で、そこが崩れると
ルールを 1 件足したのに黙って死んでいる、という形になる。
"""

from __future__ import annotations

import re
import unittest

from ccnavi.pattern import translate


class TranslateTest(unittest.TestCase):
    def hit(self, pattern: str, subject: str) -> bool:
        return re.search(translate(pattern), subject) is not None

    def test_ワイルドカードは語の中でも当たる(self):
        # `*` は任意の文字列。両隣に語の切れ目を求めると、非語文字で始まって
        # 非語文字で終わるときしか当たらないルールになる。
        for pattern, subject in (
            ("foo*bar", "foobar"),
            ("foo*bar", "fooXbar"),
            ("foo*bar", "foo-bar"),
            ("id_*sa", "id_rsa"),
            ("id_?sa", "id_rsa"),
            ("foo*", "foobar"),
            ("*bar", "foobar"),
        ):
            with self.subTest(pattern=pattern, subject=subject):
                self.assertTrue(self.hit(pattern, subject))

    def test_ワイルドカードの外では語の切れ目が効く(self):
        # ここが崩れると `sed *` が `sedate` を止めはじめる。
        for pattern, subject in (
            ("sed *", "sedate"),
            ("sed", "sedate"),
            ("git push *", "git pushx"),
            ("git push *", "mygit push"),
            (".env", "prevent"),
        ):
            with self.subTest(pattern=pattern, subject=subject):
                self.assertFalse(self.hit(pattern, subject))

    def test_素の語と末尾のワイルドカードは今までどおり(self):
        for pattern, subject in (
            ("sed *", "sed -i s/a/b/ x"),
            ("sed *", "sed"),
            ("git push *", "git push origin main"),
            ("git push *", "git push"),
            ("git push *", "/usr/bin/git push"),
            ("git push", "git   push"),
            ("secrets/", "/repo/secrets/key"),
            ("secrets/", "C:\\repo\\secrets\\key"),
            ("*.pem", "/home/u/key.pem"),
            (".claude/ccnavi/*", "/repo/.claude/ccnavi/rules.yml"),
        ):
            with self.subTest(pattern=pattern, subject=subject):
                self.assertTrue(self.hit(pattern, subject))

    def test_いま使っている書き方の翻訳は変わらない(self):
        # ルールファイルと組み込みの既定にある書き方を、翻訳した形のまま
        # 固定する。判定の当たり方を変えずに記法を直せたことの担保になる。
        expected = {
            "git push *": r"\bgit\b\s+\bpush\b\s*.*",
            "git reset --hard *": r"\bgit\b\s+\breset\b\s+\-\-\bhard\b\s*.*",
            "git branch -D *": r"\bgit\b\s+\bbranch\b\s+\-\bD\b\s*.*",
            "rm -rf *": r"\brm\b\s+\-\brf\b\s*.*",
            "sed *": r"\bsed\b\s*.*",
            ".claude/ccnavi/*": r"\.\bclaude\b[\\/]\bccnavi\b[\\/].*",
            ".claude/hooks/*": r"\.\bclaude\b[\\/]\bhooks\b[\\/].*",
            ".claude/worktrees/*": r"\.\bclaude\b[\\/]\bworktrees\b[\\/].*",
            "migrations/*": r"\bmigrations\b[\\/].*",
            # 語のあとに素の非語文字が続くところに閉じの `\b` は出ない。
            # `-` 自体が語の切れ目なので、あっても無くても当たり方は同じ。
            ".current-ticket.md": r"\.\bcurrent\-\bticket\.\bmd\b",
            ".env": r"\.\benv\b",
        }
        for pattern, want in expected.items():
            with self.subTest(pattern=pattern):
                self.assertEqual(translate(pattern), want)


if __name__ == "__main__":
    unittest.main()
