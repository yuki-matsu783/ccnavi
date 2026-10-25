"""書き込み先や削除先の途中にリンクがあるかを調べる、唯一の入口。

シンボリックリンクと junction を同じ「リンク」として扱う。道を辿ってリンクを探す判定は、
ここを通す（パスの比較を `pathcase` に通すのと同じ決まり）。調べる途中で `OSError` が出たり、
パスが root の外に出たりしたときは、拒否する側（リンクあり・外に出る）に倒す。
単発の述語 `is_link` は例外を投げず、失敗は偽を返す（`os.path.islink` と同じ）。
"""

from __future__ import annotations

import os
import stat
from collections.abc import Sequence

from . import pathcase

LINK = "link"
MISSING = "missing"
CLEAN = "clean"


def is_link(path: str) -> bool:
    """シンボリックリンクか junction。失敗は偽。"""
    return os.path.islink(path) or os.path.isjunction(path)


def has_link(root: str, path: str) -> bool:
    """root の下の path までの道（path 自身を含む）に、リンクか junction があるか。

    path をそのまま上へさかのぼり、root に着いたところで止める。root 自身と、root より上の
    リンク（macOS の `/tmp` など）は数えない。root と違う綴り（`/tmp` と `/private/tmp`）で
    渡された path も、実体が root に着けば止まる。root へのリンクを通った綴りは、そのリンクで
    真になる。root に着けない（外を指す）ときと `OSError` は真。
    """
    try:
        base = pathcase.fold(os.path.realpath(root))
        top = pathcase.fold(os.path.abspath(root))
        current = os.path.abspath(path)
        while pathcase.fold(current) != top:
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
    return False


def scan(root: str, rel_parts: Sequence[str], *, include_last: bool = True) -> tuple[str, str]:
    """root から rel_parts を下へ順に `lstat` して、`(状態, パス)` を返す。

    状態は `LINK`（最初に見つかったリンクのパスを添える）、`MISSING`（途中で `lstat` が失敗した。
    パスは空）、`CLEAN`（リンクが無い。パスは空）。`include_last` を偽にすると、最後の名前は
    見ない（`os.remove` はリンクだけを消すので、最後がリンクでも構わない場合）。
    """
    parts = list(rel_parts) if include_last else list(rel_parts)[:-1]
    current = root
    for part in parts:
        current = os.path.join(current, part)
        try:
            mode = os.lstat(current).st_mode
        except OSError:
            return MISSING, ""
        if stat.S_ISLNK(mode) or os.path.isjunction(current):
            return LINK, current
    return CLEAN, ""


def first_link(root: str, rel_parts: Sequence[str], *, include_last: bool = True) -> str | None:
    """root から rel_parts を下へ辿り、最初に見つかったリンクのパス。無ければ None。"""
    state, path = scan(root, rel_parts, include_last=include_last)
    return path if state == LINK else None


def escapes(root: str, path: str) -> bool:
    """realpath を解くと root の外か。root 自身は外ではない。解けないときは真。"""
    try:
        top = pathcase.fold(os.path.realpath(root))
        real = pathcase.fold(os.path.realpath(path))
    except (OSError, ValueError):
        return True
    return real != top and not real.startswith(top + os.sep)
