"""配布する文書（`.ccnavi/docs/*.md` と `CCNAVI.md`）が案内する `--keyword <語>` が当たること。

`--keyword` は frontmatter の `keywords` との完全一致（大文字小文字と NFC は区別しない）なので、
案内に書いた語が文書の `keywords` に無いと、引いても 0 件になる。
"""

from __future__ import annotations

import glob
import os
import re
import unicodedata
import unittest

from ccnavi.catalog import docsearch_index
from tests import ROOT

DOCS = os.path.join(ROOT, ".ccnavi", "docs")
GUIDES = [
    *sorted(glob.glob(os.path.join(DOCS, "*.md"))),
    os.path.join(ROOT, ".ccnavi", "CCNAVI.md"),
]

# `--keyword <語>` の語。`<語>` のような置き換え記号は具体語ではないので除く。
KEYWORD = re.compile(r"--keyword[ =]+([^\s`'\"<>|)）、。]+)")


# 当たらない例として書いている語（`frontmatter.md` の「keywordsの注意」）。
NOT_MEANT_TO_MATCH = {("frontmatter.md", "ログ")}


def fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def keywords_of(path: str) -> list[str]:
    with open(path, "rb") as f:
        front = docsearch_index.front_matter(f.read()) or {}
    value = front.get("keywords") or []
    return [fold(str(v)) for v in (value if isinstance(value, list) else [value])]


def matched(word: str) -> list[str]:
    """配布元の `.ccnavi/docs` で、`--keyword <word>` が当たる文書の名前（拡張子なし）。"""
    return [
        os.path.basename(p)[:-3]
        for p in sorted(glob.glob(os.path.join(DOCS, "*.md")))
        if fold(word) in keywords_of(p)
    ]


class GuidedKeywordsMatch(unittest.TestCase):
    def test_every_keyword_the_guides_name_matches_a_doc(self):
        for path in GUIDES:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            for word in KEYWORD.findall(text):
                if (os.path.basename(path), word) in NOT_MEANT_TO_MATCH:
                    continue
                with self.subTest(path=os.path.relpath(path, ROOT), keyword=word):
                    self.assertTrue(matched(word), f"--keyword {word} が当たる文書が無い")

    def test_the_words_the_proposal_guidance_names_match_the_expected_docs(self):
        """lint と `ticket start` の案内が書く 2 語は、`--keyword` の形でないので別に見る。"""
        self.assertEqual(matched("proposal-format"), ["proposal-format"])
        self.assertEqual(matched("提案書式"), ["proposal-format"])
        self.assertEqual(matched("phases"), ["grow-phases", "proposal-format"])

    def test_the_proposal_format_doc_is_guided_from_the_session_guide(self):
        with open(os.path.join(ROOT, ".ccnavi", "CCNAVI.md"), encoding="utf-8") as f:
            self.assertIn("`proposal-format.md`", f.read())
