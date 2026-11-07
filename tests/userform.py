"""テストが実行ファイルへ渡す引数に、`user` の入口の形を置く。

実行ファイルの `user` は、`--root`・`--cwd`・`--record-writes`・`--record-tree`・`--entry` を
読み飛ばした後の最初の語でなければならない。テストのヘルパーは、`--guard-ticket-approval` など
読み飛ばされないオプションを先頭に付けて呼ぶので、そのまま `user …` を後ろに渡すと入口として
読まれない。`place` は、`user <操作> [値]` を先頭のラッパーのオプションのすぐ後ろへ寄せ、
ヘルパーのオプションをその後ろに置く。

表（読み飛ばすオプション・操作の語）はテストの側に持つ。実行ファイルの表
（go/internal/tickets/model/useronly）と食い違うと、入口に届かないテストが落ちて気づく。

このファイルは標準ライブラリだけを読む。sh の代役が `python userform.py <引数>…` で起こし、
`--entry` の並びを実行ファイルと同じに検べる（`take_entry`。断る並びなら終了コード 2）。
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

USER_WORD = "user"
# sh と拡張が実行ファイルを呼ぶときに、先に付ける値つきのオプション。
WRAPPER_OPTIONS = ("--root", "--cwd", "--record-writes", "--record-tree", "--entry")
# `user` の後ろの操作の語と、値を 1 つ取るか（`user reviewed 3`）。
VERBS: dict[tuple[str, ...], bool] = {
    ("agree",): False,
    ("reviewed",): True,
    ("close-early",): False,
    ("prune",): False,
    ("handoff",): False,
    ("mirror",): False,
}
ENTRY_OPTION = "--entry"
ENTRY_VALUES = ("sh", "board")


def place(prefix: Sequence[str], args: Sequence[str]) -> list[str]:
    """`prefix`（ヘルパーが付けるオプション）と `args`（呼び出し側の引数）を 1 つの引数列にする。

    `args` が `user` で始まる（先頭のラッパーのオプションの後ろ）ときだけ並べ替える。
    それ以外は `[*prefix, *args]` のまま。
    """
    wrapper_prefix, other_prefix = _split_wrapper(prefix)
    args = list(args)
    at = 0
    while at < len(args) and args[at] in WRAPPER_OPTIONS and at + 1 < len(args):
        at += 2
    if at >= len(args) or args[at].casefold() != USER_WORD:
        return [*prefix, *args]
    words = [w.casefold() for w in args[at + 1 : at + 3]]
    size = 1
    for verb, value in VERBS.items():
        if tuple(words[: len(verb)]) == verb:
            size = 1 + len(verb) + (1 if value else 0)
            break
    return [
        *wrapper_prefix,
        *args[:at],
        *args[at : at + size],
        *other_prefix,
        *args[at + size :],
    ]


def _split_wrapper(prefix: Sequence[str]) -> tuple[list[str], list[str]]:
    wrapper: list[str] = []
    other: list[str] = []
    at = 0
    while at < len(prefix):
        pair = list(prefix[at : at + 2])
        (wrapper if prefix[at] in WRAPPER_OPTIONS else other).extend(pair)
        at += 2
    return wrapper, other


class UserEntryError(Exception):
    """`--entry` の並びを実行ファイルが断る。"""


def _is_wrapper_option(word: str) -> bool:
    return word in WRAPPER_OPTIONS or "=" in word and word.partition("=")[0] in WRAPPER_OPTIONS


def take_entry(argv: list[str]) -> tuple[list[str], str]:
    """先頭のラッパーのオプションから `--entry <値>` を外し、argv と値（無ければ空）を返す。

    `user` の入口の前に付けた形だけを読む。値が `ENTRY_VALUES` に無い、2 度ある、`user` の入口で
    ない、のいずれも `UserEntryError`（実行ファイルと同じ）。
    """
    kept: list[str] = []
    marker = ""
    at = 0
    while at < len(argv) and _is_wrapper_option(argv[at]):
        word = argv[at]
        if word.partition("=")[0] != ENTRY_OPTION:
            step = 1 if "=" in word else 2
            kept += argv[at : at + step]
            at += step
            continue
        if marker:
            raise UserEntryError(f"{ENTRY_OPTION} は 1 度しか渡せない")
        if "=" in word:
            marker, at = word.partition("=")[2], at + 1
        elif at + 1 < len(argv):
            marker, at = argv[at + 1], at + 2
        else:
            raise UserEntryError(f"{ENTRY_OPTION} には値が要る")
        if marker not in ENTRY_VALUES:
            raise UserEntryError(f"{ENTRY_OPTION} に渡せるのは {' か '.join(ENTRY_VALUES)}")
    # 比べるのはコマンドの語なので、ASCII の大文字だけを小文字にする（casefold は ſ を s にする）。
    first = argv[at] if at < len(argv) else ""
    if marker and not (first.isascii() and first.lower() == USER_WORD):
        raise UserEntryError(f"{ENTRY_OPTION} は user の入口と一緒に渡す")
    return kept + argv[at:], marker


if __name__ == "__main__":
    try:
        take_entry(sys.argv[1:])
    except UserEntryError as e:
        print(e, file=sys.stderr)
        sys.exit(2)
