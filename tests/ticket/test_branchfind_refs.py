"""依頼文から issue・MR の指定を見つける正規表現のヒットと外れ。

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
            # 番号の直後が助詞・括弧・読点のときも拾う
            "#152を直して": [(ISSUE, 152)],
            "「#152」の件": [(ISSUE, 152)],
            "issue 152 を対応": [(ISSUE, 152)],
            "!5 の指摘": [(MR, 5)],
            "#12、#13。": [(ISSUE, 12), (ISSUE, 13)],
            "PR #12": [(MR, 12)],
            # 助詞・全角の句読点・語尾。助詞の直後の `#` も読む
            "#152から着手": [(ISSUE, 152)],
            "#152です": [(ISSUE, 152)],
            "#152！": [(ISSUE, 152)],
            "これは#152です": [(ISSUE, 152)],
            "#152と#153": [(ISSUE, 152), (ISSUE, 153)],
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
            # 番号が別の語の一部（直前が語、直後が助詞以外の文字）
            "手順#2を見直して",
            "issue 2つ目",
            "PR 3本",
            "MR 3件",
            "#2つ目",
            "#2-3 の範囲",
            "issue 152(仮)",
            # インラインコード
            "`#12` は設定の例",
            "`!5` と `issue 7`",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(refs(text), [])

    def test_a_number_before_a_particle_is_read_even_in_a_longer_phrase(self):
        # 「2 のうち 1 つ」の意図は読み分けられない。今の挙動（番号の直後が助詞なので issue 2 を
        # 拾う）を固定する。拾ってもユーザに確かめる指示が足されるだけで、作業は止まらない。
        self.assertEqual(refs("issue 2のうち1つ"), [(ISSUE, 2)])

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


# sh 側（tests/sh/test_branches_sh.py）にも同じ表がある。規則を変えたら両方を直す。
# （名前, issue の番号, 名前に番号を含むか）
NAME_CASES = (
    ("feature-102-main-sh-の使い方の表示を-1-行足す-c3-検証用", 1, False),
    ("feature-102-main-sh-の使い方の表示を-1-行足す-c3-検証用", 102, True),
    ("feature-1-login", 1, True),
    ("fix-1-typo", 1, True),
    ("fix-1-typo", 2, False),
    ("fix/152-y", 152, True),
    ("issue-152", 152, True),
    ("x-152", 152, True),
    ("other-102-a-1-b", 1, True),
)


class NamesNumberTest(unittest.TestCase):
    def test_the_number_is_set_off_by_separators_or_the_ends(self):
        for name in (
            "feature-152-login",
            "152-login",
            "fix/152-typo",
            "x-152",
            "issue-152",
            "feature_152_x",
            "v1.152.x",
        ):
            with self.subTest(name=name):
                self.assertTrue(branchfind.names_number(name, 152))

    def test_a_letter_or_digit_next_to_the_number_does_not_match(self):
        for name in ("feature-1520-x", "feature-2152-x", "v15-2", "main", "i152", "152a"):
            with self.subTest(name=name):
                self.assertFalse(branchfind.names_number(name, 152))

    def test_random_suffixes_of_web_session_branches_do_not_match(self):
        self.assertFalse(branchfind.names_number("claude/tender-volta-qmy99q", 99))
        self.assertFalse(branchfind.names_number("claude/zesty-a5b-x5y7z9", 5))

    def test_a_name_in_the_ccnavi_form_is_read_by_its_number_only(self):
        slug = "feature-102-main-sh-の使い方の表示を-1-行足す-c3-検証用"
        for name, number, expected in NAME_CASES:
            with self.subTest(name=name, number=number):
                self.assertEqual(branchfind.names_number(name, number), expected)
        self.assertFalse(branchfind.names_number(slug, 1))

    def test_the_first_word_comes_from_the_given_prefixes(self):
        self.assertFalse(branchfind.names_number("story-102-a-1-b", 1, ("story",)))
        self.assertTrue(branchfind.names_number("story-102-a-1-b", 1))


if __name__ == "__main__":
    unittest.main()
