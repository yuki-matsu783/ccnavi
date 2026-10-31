"""出力の行頭の `次: ` を、文字列リテラルで直接組み立てるコードを増やさない。

`次:` の文は文面ファイル（`.ccnavi/messages/next-*.yml`）に置き、`texts.say` で引く
（script-output.md）。Python が `"次: …"` と書いて出す箇所は、文面ファイルへまだ移していないものだけを許可リストに置く。
新しい直書きが増えたら落ちる。移して減らしたときも、件数を直さないと落ちる（直し方は失敗の文に書く）。

見る対象は `src/ccnavi/` の .py の文字列リテラルで、次のどちらか。
- 値が `次: ` で始まり、続きの文字がある（f 文字列は、先頭の部分が `次: ` ならそれだけで数える）
- 値の途中の改行の直後に `次: ` がある

docstring・コメント・`startswith("次: ")` のような見出しだけの比較は数えない。
"""

from __future__ import annotations

import ast
import glob
import os
import unittest

from tests import ROOT

SRC = os.path.join(ROOT, "src", "ccnavi")
LABEL = "次: "

# 直書きを残してよい箇所（src/ccnavi からの相対パス → 件数）。文面ファイルへ移したら外す。
# message-migration.md の「残り」の表と食い違わないようにする。
ALLOWED: dict[str, int] = {
    # 計画の誤りで断るときの `次:`（日本語の直書き。1 まとまりずつ移す）
    "tickets/proposal/propose.py": 1,
}


def _docstrings(tree: ast.AST) -> set[int]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                found.add(id(body[0].value))
    return found


def _is_literal_next(text: str) -> bool:
    """f 文字列でない 1 つの文字列。見出しだけの `次: ` は比較に使うので数えない。"""
    if text.startswith(LABEL) and len(text) > len(LABEL):
        return True
    return f"\n{LABEL}" in text


def _is_fstring_next(node: ast.JoinedStr) -> bool:
    pieces = [p.value for p in node.values if isinstance(p, ast.Constant)]
    if not pieces:
        return False
    first = node.values[0]
    starts = isinstance(first, ast.Constant) and str(first.value).startswith(LABEL)
    return starts or any(f"\n{LABEL}" in str(p) for p in pieces)


def literal_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in sorted(glob.glob(os.path.join(SRC, "**", "*.py"), recursive=True)):
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        skip = _docstrings(tree)
        inside_fstring = {
            id(part)
            for node in ast.walk(tree)
            if isinstance(node, ast.JoinedStr)
            for part in node.values
        }
        hits = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                hits += _is_fstring_next(node)
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in skip
                and id(node) not in inside_fstring
                and _is_literal_next(node.value)
            ):
                hits += 1
        if hits:
            counts[os.path.relpath(path, SRC).replace(os.sep, "/")] = hits
    return counts


class NextLineLiteralTest(unittest.TestCase):
    def test_literal_next_lines_stay_within_the_allowed_list(self):
        extra = {name: n for name, n in literal_counts().items() if n > ALLOWED.get(name, 0)}
        self.assertEqual(
            {},
            extra,
            "`次: ` の文を Python に直書きする箇所が増えた。文を文面ファイル"
            "（.ccnavi/messages/next-cli.yml など）に足し、texts.say で引く"
            "（.agents/docs/message-files.md）。どうしても直書きが要るなら、"
            "理由を書いて ALLOWED に足し、message-migration.md の「残り」と合わせる",
        )

    def test_the_allowed_list_has_no_stale_entries(self):
        found = literal_counts()
        stale = {name: n for name, n in ALLOWED.items() if found.get(name, 0) < n}
        self.assertEqual(
            {},
            stale,
            "直書きが許可リスト（ALLOWED）の件数より減った。文面ファイルへ移したなら、"
            "ALLOWED の件数を直し、移した識別子を message-migration.md の「移した」へ動かす",
        )


if __name__ == "__main__":
    unittest.main()
