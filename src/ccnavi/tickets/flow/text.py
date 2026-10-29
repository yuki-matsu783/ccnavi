"""フローに書かれた文字列の整え方。制御文字を除き、長さを切り、印や囲みのなりすましを崩す。

フローの値はユーザかエージェントが書いたもので、文面に混ぜる前に必ずここを通す。
flow から分けた。flow を読まない。
"""

from __future__ import annotations

import unicodedata

# 1 行に載せる文の長さの上限。
TEXT_LIMIT = 120

# フローの文に ccnavi の接頭辞を真似させない。大文字小文字・空白・結合文字・書式の制御を無視して、
# `[` の直後が `ccnavi` で始まる括弧は、亀甲括弧 `〔…〕` に置き換える。
# キリル文字・全角の括弧など、見た目だけが似た別の字には対応しない。ASCII の素の混入だけを中和する。
_BADGE_WORD = "ccnavi"
_BADGE_OPEN = "〔"
_BADGE_CLOSE = "〕"
# 括弧の閉じを探す範囲（元の文字数）。
_BADGE_REACH = 64
# 案内の区切りの行に似せた文。フローの文の中に出たら置き換える。
# プロジェクトのスキルの目録（projskills.FENCE_OPEN / FENCE_CLOSE）の区切りも同じく置き換える。
_FENCE_PHRASES = (
    "ここから合意済みのフローの本文",
    # 以前の区切りの文。コピーされた古いフローの中でも置き換える。
    "ここからユーザが書いたフローの本文",
    "フローの本文ここまで",
    "ここからプロジェクトのスキルの目録",
    "目録ここまで",
)
_FENCE_SHOWN = "〔区切りに似た文〕"


# ---- 並べる


def clean(text) -> str:
    """文脈に載せる値から、改行と制御文字（書式の制御も）を除く。"""
    shown = str(text if text is not None else "")
    return "".join(
        " " if ch in "\r\n\t\v\f\x85  " else ch
        for ch in shown
        if ch in "\r\n\t\v\f\x85  " or unicodedata.category(ch) not in ("Cc", "Cf")
    )


def _text(value) -> str:
    """文字列か数だけを文にする。リストやマップは中身を辿らない（深い入れ子で失敗しない）。"""
    if isinstance(value, bool):
        return ""
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e21:
        # 整数の値の小数（`1.0`）は整数の表記にする。ボード（JavaScript の `String`）と揃える。
        return str(int(value))
    if isinstance(value, (str, int, float)):
        return str(value)
    return ""


def _line(value) -> str:
    """1 行にまとめて切る。ccnavi の接頭辞は真似させない。"""
    if isinstance(value, Exception):
        value = str(value)
    text = value if isinstance(value, str) else _text(value)
    shown = " ".join(clean(text[: TEXT_LIMIT * 8]).split())
    shown = _neutral(shown)
    if len(shown) > TEXT_LIMIT:
        shown = shown[:TEXT_LIMIT] + "…"
    return shown


_MARKS = ("Mn", "Mc", "Me")
_IGNORED = (*_MARKS, "Cf", "Cc", "Zs")


def _skeleton(text: str) -> tuple[str, list[int]]:
    """比べるための表記と、表記の 1 字ごとの元の位置を返す。

    1 字ずつ正準分解（NFD）して、正準等価（NFC と NFD）の違いをそろえる。結合文字・書式の制御・
    制御文字・空白を除き、大文字小文字をそろえる。
    """
    chars: list[str] = []
    origin: list[int] = []
    for i, ch in enumerate(text):
        for part in unicodedata.normalize("NFD", ch):
            if part.isspace() or unicodedata.category(part) in _IGNORED:
                continue
            for folded in part.casefold():
                chars.append(folded)
                origin.append(i)
    return "".join(chars), origin


def _neutral(text: str) -> str:
    """フローの文が ccnavi の知らせや案内の区切りに見えないようにする。

    - `[` の直後が `ccnavi` で始まる括弧は、開きと、その先の最初の閉じ `]` を `〔` `〕` にする。
      `[ccnavi dry-run]`・`[CCNAVI]`・空白や結合文字を挟んだものも同様に扱う
    - 案内の区切りの行の文（`_FENCE_PHRASES`）は `_FENCE_SHOWN` に置き換える
    """
    skeleton, origin = _skeleton(text)
    replace: dict[int, str] = {}
    word = _BADGE_WORD
    at = skeleton.find("[")
    while at >= 0:
        if skeleton.startswith(word, at + 1):
            start = origin[at]
            replace[start] = _BADGE_OPEN
            close = skeleton.find("]", at + 1)
            if close >= 0 and origin[close] - start <= _BADGE_REACH:
                replace[origin[close]] = _BADGE_CLOSE
        at = skeleton.find("[", at + 1)
    for phrase in _FENCE_PHRASES:
        needle = _skeleton(phrase)[0]
        at = skeleton.find(needle)
        while at >= 0:
            first, last = origin[at], origin[at + len(needle) - 1]
            while last + 1 < len(text) and unicodedata.category(text[last + 1]) in _MARKS:
                last += 1  # 末尾の文字に付いた結合文字（分解した濁点など）も置き換えに含める
            replace[first] = _FENCE_SHOWN
            for i in range(first + 1, last + 1):
                replace[i] = ""
            at = skeleton.find(needle, at + len(needle))
    if not replace:
        return text
    return "".join(replace.get(i, ch) for i, ch in enumerate(text))


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _capped(parts: list[str], total: int) -> list[str]:
    """リストを ITEM_LIMIT で切り、残りの数をつける。"""
    if total > len(parts):
        return parts + [f"…ほか {total - len(parts)} 件"]
    return parts
