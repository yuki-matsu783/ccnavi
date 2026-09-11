"""ルールが指すファイルを additionalContext に載せる（REQ-PRE-12）。

`additionalContextFile` と `additionalContextOnceFile` は、文の代わりに（または文に
続けて）ファイルの本文をモデルへ渡す。長い案内を rules.yml に抱えず、既にある md を
そのまま指すためのもの。

読むのはワークスペースの中だけ。ルールから任意のファイルをモデルに流し込める形に
しない。絶対パスと `..` で上に出るパスは lint が止め、実行時も読まない。

読む長さは固定の上限で切る。切ったときはそのことを本文の末尾に添える。黙って切ると、
モデルは途中で終わる文を「全部」だと思って読む。続きはファイルを読めば手に入るので、
そう言う。
"""

from __future__ import annotations

import os
from typing import TextIO

# 1 回の応答に載せる本文の上限（文字数）。固定。超えた分は載せず、切ったことを言う。
MAX_CHARS = 4000


def bad_path(rel: str) -> str:
    """この欄の値がワークスペースの中を指せない理由。問題なければ空。"""
    if not rel:
        return ""
    slashed = rel.replace("\\", "/")
    if os.path.isabs(rel) or slashed.startswith("/") or (len(slashed) > 1 and slashed[1] == ":"):
        return "絶対パスは使えない。ルートからの相対パスで書く"
    if any(part == ".." for part in slashed.split("/")):
        return "`..` で上に出るパスは使えない"
    return ""


def locate(bases: list[str], rel: str) -> str:
    """候補のルートを順に見て、最初に在ったファイルの場所。無ければ空。"""
    if not rel or bad_path(rel):
        return ""
    for base in bases:
        full = os.path.join(base, rel.replace("/", os.sep))
        if os.path.isfile(full):
            return full
    return ""


def load(stderr: TextIO, bases: list[str], rel: str) -> str:
    """ファイルの本文。無ければ空。上限を超えたら先頭だけを返し、切ったことを末尾に添える。"""
    full = locate(bases, rel)
    if not full:
        return ""
    try:
        with open(full, encoding="utf-8", errors="replace") as f:
            head = f.read(MAX_CHARS + 1)
    except OSError as exc:
        stderr.write(f"ccnavi: {rel} を読めない: {exc}\n")
        return ""
    if len(head) <= MAX_CHARS:
        return head.strip()
    return (
        head[:MAX_CHARS].rstrip()
        + f"\n\n(ccnavi: {rel} は {MAX_CHARS} 文字を超えるので先頭だけを載せた。"
        "続きはこのファイルを読むこと)"
    )


def over_limit(full: str) -> bool:
    """このファイルは上限を超えるか。読めなければ False（無いのと同じ扱い）。"""
    try:
        with open(full, encoding="utf-8", errors="replace") as f:
            return len(f.read(MAX_CHARS + 1)) > MAX_CHARS
    except OSError:
        return False
