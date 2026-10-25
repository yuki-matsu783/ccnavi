"""書き込み先や削除先までの道に、リンクがあるかを調べる唯一の入口。

シンボリックリンクと junction を同じ「リンク」として扱う。道をたどってリンクを探す判定は、
必ずここを通す。パスの比較を `pathcase` に通すのと同じ決まりである。
`has_link` と `escapes` は、調べる途中で `OSError` が出たときや、パスが root の外に出るときに、
拒否する側（リンクあり・外に出る）に倒す。`scan` は `lstat` の失敗を `MISSING` として返し、
呼び出し側が決める。1 つのパスだけを見る述語 `is_link` は例外を投げず、失敗したときは偽を返す
（`os.path.islink` と同じ）。
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
    """シンボリックリンクか junction。失敗したときは偽。"""
    return os.path.islink(path) or os.path.isjunction(path)


def has_link(root: str, path: str) -> bool:
    """root の下にある path までの道（path 自身を含む）に、リンクか junction があるかを返す。

    path から上へさかのぼり、root に着いたところで止める。root 自身と、root より上のリンク
    （macOS の `/tmp` など）は数えない。root と違う綴りで渡された path（`/tmp` と `/private/tmp`
    など）も、実体が root に着けば止まる。root へのリンクを通った綴りは、そのリンクがあるので真に
    なる。root に着けないとき（外を指すとき）と `OSError` のときは真を返す。
    """
    try:
        base = pathcase.fold(os.path.realpath(root))
        top = pathcase.fold(os.path.abspath(root))
        current = os.path.abspath(path)
        while pathcase.fold(current) != top:
            if is_link(current):
                return True
            # root と違う綴りのときだけ、実体が root に着けば止まる。root の下の綴りなら
            # root まで 1 段ずつ調べる（途中のリンクが root の中へ戻っても見逃さない）。
            outside_spelling = not pathcase.fold(current).startswith(top.rstrip(os.sep) + os.sep)
            if outside_spelling and pathcase.fold(os.path.realpath(current)) == base:
                return False
            parent = os.path.dirname(current)
            if parent == current:
                return True
            current = parent
    except (OSError, ValueError):
        return True
    return False


def scan(root: str, rel_parts: Sequence[str], *, include_last: bool = True) -> tuple[str, str]:
    """root から rel_parts を下へ順に `lstat` し、`(状態, パス)` を返す。

    状態は `LINK`（最初に見つかったリンクのパスを添える）、`MISSING`（途中で `lstat` が失敗した。
    パスは空）、`CLEAN`（リンクが無い。パスは空）のどれか。`include_last` を偽にすると、最後の名前は
    調べない。`os.remove` はリンクだけを消すので、最後の名前がリンクでも構わない場合に使う。
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
    """root から rel_parts を下へたどり、最初に見つかったリンクのパスを返す。

    無ければ None を返す。
    """
    state, path = scan(root, rel_parts, include_last=include_last)
    return path if state == LINK else None


def escapes(root: str, path: str) -> bool:
    """realpath で解決すると root の外に出るかを返す。

    root 自身は外ではない。解決できないときは真を返す。
    """
    try:
        top = pathcase.fold(os.path.realpath(root))
        real = pathcase.fold(os.path.realpath(path))
    except (OSError, ValueError):
        return True
    return real != top and not real.startswith(top + os.sep)
