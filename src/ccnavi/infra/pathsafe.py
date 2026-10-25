"""書き込み先や削除先の「途中にリンクがあるか」の判定。

シンボリックリンクと junction を同じに扱う。道を辿ってリンクを調べる処理は、必ずここを通す
（呼び出し側ごとに書かない。読み方が食い違う）。パスの比較は `pathcase.fold` を通す。
root は信頼する起点で、root 自身と、root より上のリンク（macOS の `/tmp` など）は数えない。
調べる途中で失敗したときは、拒否する側（リンクあり・外）として扱う。
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from . import pathcase


def is_link(path: str) -> bool:
    """シンボリックリンクか junction。"""
    return os.path.islink(path) or os.path.isjunction(path)


def has_link(root: str, path: str) -> bool:
    """root の下の path までの道（path 自身を含む）に、リンクか junction があるか。

    path から上へさかのぼり、root に着いたところで止める。root と違う綴りで渡された path
    （`/tmp` と `/private/tmp` など）でも、実体が root に着けば止まる。root の外を指す path
    （着けないまま最上位に達する）と、調べる途中の失敗は、リンクありとして扱う。
    """
    try:
        top = pathcase.fold(os.path.abspath(root))
        base = pathcase.fold(os.path.realpath(root))
        current = os.path.abspath(path)
        while True:
            if pathcase.fold(current) == top:
                return False
            if is_link(current):
                return True
            if pathcase.fold(os.path.realpath(current)) == base:
                return False
            parent = os.path.dirname(current)
            if parent == current:
                return True
            current = parent
    except (OSError, ValueError):
        return True


def first_link(root: str, rel_parts: Sequence[str], *, include_last: bool = True) -> str | None:
    """root から rel_parts を下へ辿り、最初に見つかったリンクか junction のパス。無ければ None。

    返すのは root に名前を足していったパス（リンクを解かない）。存在しない名前はリンクではない。
    `include_last=False` なら最後の名前は見ない（`os.remove` はリンクだけを消すので、
    最後の名前がリンクでも構わない処理のため）。
    """
    names = rel_parts if include_last else rel_parts[:-1]
    here = root
    for name in names:
        here = os.path.join(here, name)
        if is_link(here):
            return here
    return None


def escapes(root: str, path: str) -> bool:
    """realpath を解くと path が root の外か。root 自身は中。解けなければ外。"""
    try:
        base = pathcase.fold(os.path.realpath(root))
        real = pathcase.fold(os.path.realpath(path))
    except (OSError, ValueError):
        return True
    return real != base and not real.startswith(base.rstrip(os.sep) + os.sep)
