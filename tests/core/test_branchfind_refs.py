"""依頼文から issue・MR の指定を見つける正規表現の当たりと外れ。

UserPromptSubmit の hook は、ここで見つけた指定ごとに「紐づくブランチを探してユーザに確かめる」
指示を足す。外れ（見出しの `#`、色の `#fff`、`C#` など）で指示を足すと、関係の無い依頼のたびに
ブランチを探させることになるので、外れも表で固定する。
"""

from __future__ import annotations

import unittest

from ccnavi.tickets import branchfind
from ccnavi.tickets.branchfind import ISSUE, MR, Ref


def refs(text):
    return [(r.kind, r.number) for r in branchfind.prompt_refs(text)]


class PromptRefsTest(unittest.TestCase):
    def test_hits(self):
        cases = {
            "#152 を直して": [(ISSUE, 152)],
            "＃152 を直して": [(ISSUE, 152)],
            "issue 152 をお願い": [(ISSUE, 152)],
            "Issue #7 の続き": [(ISSUE, 7)],
            "issue: 42": [(ISSUE, 42)],
            "issue-152 のブランチ": [(ISSUE, 152)],
            "issues/152 を見て": [(ISSUE, 152)],
            "(#9) を直す": [(ISSUE, 9)],
            "https://github.com/acme/w/issues/152 を直して": [(ISSUE, 152)],
            "https://gitlab.com/g/p/-/issues/31": [(ISSUE, 31)],
            "https://github.com/acme/w/pull/5/files を見て": [(MR, 5)],
            "https://gitlab.com/g/sub/p/-/merge_requests/9": [(MR, 9)],
            "!5 のレビュー": [(MR, 5)],
            "（!6）": [(MR, 6)],
            "MR 5 を直して": [(MR, 5)],
            "MR !5": [(MR, 5)],
            "PR #12 の指摘": [(MR, 12)],
            "このPR 3を": [(MR, 3)],
            "pull request 4": [(MR, 4)],
            "マージリクエスト!4": [(MR, 4)],
            "プルリク 8 の続き": [(MR, 8)],
            "#3 と !4": [(ISSUE, 3), (MR, 4)],
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(refs(text), want)

    def test_misses(self):
        cases = [
            "# 見出し\n本文",
            "## 2. 手順",
            "色は #fff と #a1b2c3",
            "color: #333333; background-color: #123456",
            "#000 と #012345（0 で始まる）",
            "C# 5 と F# の話",
            "C#5",
            "&#123; は文字参照",
            "##12",
            "page#5 の節",
            "すごい!5",
            "!!5",
            "```\n# コメント #99\n```",
            "Mr. Smith",
            "issue を探して",
            "#12a",
            "",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(refs(text), [])

    def test_a_url_is_read_once(self):
        # URL の断片（#issuecomment-…）や URL の中の番号を、語や `#` としてもう 1 度数えない
        text = "https://github.com/acme/w/issues/152#issuecomment-1 と issue 152"
        self.assertEqual(refs(text), [(ISSUE, 152)])
        self.assertEqual(branchfind.prompt_refs(text)[0].repo, "acme/w")

    def test_the_same_number_is_folded_and_the_list_is_bounded(self):
        self.assertEqual(refs("#7 と issue 7 と #7"), [(ISSUE, 7)])
        many = " ".join(f"#{n}" for n in range(1, 20))
        self.assertEqual(len(branchfind.prompt_refs(many)), branchfind.MAX_REFS)

    def test_labels(self):
        self.assertEqual(Ref(ISSUE, 152).label(), "issue #152")
        self.assertEqual(Ref(MR, 5, "acme/w").label(), "MR !5（acme/w）")


class NamesNumberTest(unittest.TestCase):
    def test_the_number_must_not_touch_other_digits(self):
        for name in ("feature-152-login", "152-login", "fix/152-typo", "x-152", "issue-152"):
            with self.subTest(name=name):
                self.assertTrue(branchfind.names_number(name, 152))
        for name in ("feature-1520-x", "feature-2152-x", "v15-2", "main"):
            with self.subTest(name=name):
                self.assertFalse(branchfind.names_number(name, 152))


if __name__ == "__main__":
    unittest.main()
