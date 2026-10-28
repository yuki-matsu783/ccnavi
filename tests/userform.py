"""テストが実行ファイルへ渡す引数に、`user` の入口の形を置く。

実行ファイルの `user` は、`--root`・`--cwd`・`--record-writes`・`--record-tree` を読み飛ばした
後の最初の語でなければならない（`ccnavi.tickets.model.user_only.rewrite`）。テストのヘルパーは、
`--guard-ticket-approval` など読み飛ばされないオプションを先頭に付けて呼ぶので、そのまま
`user …` を後ろに渡すと入口として読まれない。`place` は、`user <操作> [値]` を先頭の
ラッパーのオプションのすぐ後ろへ寄せ、ヘルパーのオプションをその後ろに置く。
"""

from __future__ import annotations

from collections.abc import Sequence

from ccnavi.tickets.model import user_only


def place(prefix: Sequence[str], args: Sequence[str]) -> list[str]:
    """`prefix`（ヘルパーが付けるオプション）と `args`（呼び出し側の引数）を 1 つの引数列にする。

    `args` が `user` で始まる（先頭のラッパーのオプションの後ろ）ときだけ並べ替える。
    それ以外は `[*prefix, *args]` のまま。
    """
    wrapper_prefix, other_prefix = _split_wrapper(prefix)
    args = list(args)
    at = 0
    while at < len(args) and args[at] in user_only.WRAPPER_OPTIONS and at + 1 < len(args):
        at += 2
    if at >= len(args) or args[at].casefold() != user_only.USER_WORD:
        return [*prefix, *args]
    words = [w.casefold() for w in args[at + 1 : at + 3]]
    size = 1
    for verb in user_only.VERBS:
        if tuple(words[: len(verb.words)]) == verb.words:
            size = 1 + len(verb.words) + (1 if verb.value else 0)
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
        (wrapper if prefix[at] in user_only.WRAPPER_OPTIONS else other).extend(pair)
        at += 2
    return wrapper, other
