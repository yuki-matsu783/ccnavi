"""やさしいパターン記法を正規表現に翻訳する。

記法をわざと小さくしてあるのは、ルールファイルを読んで見直すのが人だから。
書き損じた正規表現は、誰にも見えない穴になる。
"""

from __future__ import annotations

import re


def translate(pattern: str) -> str:
    """やさしいパターン記法を正規表現にする。

    `*` が任意の長さの文字列（空でもよい）、`?` がちょうど 1 文字、
    空白の並びが任意の空白の並びを表すので、"git push" は "git   push" にも当たる。

    残りはそのままの文字として扱う。結果は対象のどこかに現れるかを探すので、
    "git push" は "/usr/bin/git push origin main" にも当たる。

    語の切れ目は、素の語の文字と柔らかい部分が接するところに自動で入れる。
    これが "sed *" を "sedate" に当てず、素の "sed" には当てる仕組み。
    """
    out: list[str] = []
    in_word = False  # 直前の文字が素の語の文字だった

    def flush() -> None:
        nonlocal in_word
        if in_word:
            out.append(r"\b")
            in_word = False

    i = 0
    while i < len(pattern):
        c = pattern[i]

        if c == "*":
            flush()
            out.append(".*")

        elif c == "?":
            flush()
            out.append(".")

        elif c.isspace():
            flush()
            j = i
            while j < len(pattern) and pattern[j].isspace():
                j += 1
            # ワイルドカードに続く空白は省いてよい。
            # "git push *" が素の "git push" も拾えるように。
            out.append(r"\s*" if j < len(pattern) and pattern[j] == "*" else r"\s+")
            i = j
            continue

        elif c == "/":
            # ルールは 1 回書いて、どの機械でも効かなければならない。
            # パスの区切りはどちらの綴りにも当てる。
            flush()
            out.append(r"[\\/]")

        else:
            if _is_word(c) and not in_word:
                out.append(r"\b")
            out.append(re.escape(c))
            in_word = _is_word(c)

        i += 1

    flush()
    return "".join(out)


def _is_word(c: str) -> bool:
    return c == "_" or c.isalnum()
