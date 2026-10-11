"""フローに書かれた文字列の整え方。制御文字を除き、長さを切り、印や囲みのなりすましを崩す。

フローの値はユーザかエージェントが書いたもので、文面に混ぜる前に必ずここを通す。
flow から分けた。flow を読まない。
"""

from __future__ import annotations

import unicodedata

# 1 行に載せる文の長さの上限。
TEXT_LIMIT = 120

# フローの文の中で ccnavi の接頭辞を真似させない。`[` / `［` の直後が（互換文字・書式の制御・
# 結合文字・似た形の字をそろえて）`ccnavi` で始まる括弧は、亀甲括弧 `〔…〕` に置き換える。
_BADGE_WORD = "ccnavi"
_BADGE_OPEN = "〔"
_BADGE_CLOSE = "〕"
# 括弧の閉じを探す範囲（元の文字数）。
_BADGE_REACH = 64
# ラテン文字に似た形の字（キリル・ギリシャ・アルメニアなど）。`ccnavi` の表記に要る字だけ。
_CONFUSABLE = {
    "\u0441": "c",  # с キリル
    "\u0421": "c",  # С
    "\u03f2": "c",  # ϲ ギリシャ
    "\u03f9": "c",  # Ϲ
    "\u217d": "c",  # ⅽ
    "\u0430": "a",  # а キリル
    "\u0410": "a",  # А
    "\u03b1": "a",  # α
    "\u0391": "a",  # Α
    "\u043f": "n",  # п キリル（小文字の n に似る）
    "\u0578": "n",  # ո アルメニア
    "\u03b7": "n",  # η
    "\u0274": "n",  # ɴ
    "\u03bd": "v",  # ν ギリシャ
    "\u0475": "v",  # ѵ キリル
    "\u0474": "v",  # Ѵ
    "\u2174": "v",  # ⅴ
    "\u0456": "i",  # і キリル
    "\u0406": "i",  # І
    "\u03b9": "i",  # ι
    "\u0399": "i",  # Ι
    "\u0131": "i",  # ı
    "\u04cf": "i",  # ӏ
    "\u2170": "i",  # ⅰ
    "\u217c": "i",  # ⅼ
    "\u01c0": "i",  # ǀ
}
# 案内の区切りの行に似せた文。フローの文の中に出たら置き換える。
# プロジェクトのスキルの目録（projskills.FENCE_OPEN / FENCE_CLOSE）の区切りも同じく置き換える。
_FENCE_PHRASES = (
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
    """文字列か数だけを文にする。リストや辞書は中身を辿らない（深い入れ子で失敗しない）。"""
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


_IGNORED = ("Mn", "Mc", "Me", "Cf", "Cc", "Zs")


def _skeleton(text: str) -> tuple[str, list[int]]:
    """見た目で比べるための表記と、その 1 字ずつの元の位置。

    互換分解（NFKD。全角の `［` は `[`、`ⅽ` は `c`）し、結合文字・書式の制御・制御文字・
    空白を除き、似た形の字（`_CONFUSABLE`）をラテン文字に置き換え、大文字小文字をそろえる。
    """
    chars: list[str] = []
    origin: list[int] = []
    for i, ch in enumerate(text):
        for part in unicodedata.normalize("NFKD", ch):
            if part.isspace() or unicodedata.category(part) in _IGNORED:
                continue
            for folded in _CONFUSABLE.get(part, part).casefold():
                chars.append(_CONFUSABLE.get(folded, folded))
                origin.append(i)
    return "".join(chars), origin


def _neutral(text: str) -> str:
    """フローの文が ccnavi の知らせや案内の区切りに見えないようにする（L-a）。

    - `[` / `［`（互換文字も）の直後が `ccnavi` で始まる括弧は、開きと、その先の最初の閉じ
      （`]` / `］`）を `〔` `〕` に置き換える。`[ccnavi dry-run]`・`[CCNAVI]`・幅の無い字や
      結合文字を挟んだもの・キリル文字の `с` で書いたものも同じ
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
            replace[first] = _FENCE_SHOWN
            for i in range(first + 1, last + 1):
                replace[i] = ""
            at = skeleton.find(needle, at + len(needle))
    if not replace:
        return text
    return "".join(replace.get(i, ch) for i, ch in enumerate(text))


def impersonates(text: str) -> bool:
    """文に ccnavi の接頭辞か案内の区切りに見える箇所が残っているか。テストと確かめ用。"""
    skeleton, _ = _skeleton(text)
    if any(_skeleton(p)[0] in skeleton for p in _FENCE_PHRASES):
        return True
    return f"[{_BADGE_WORD}" in skeleton


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _capped(parts: list[str], total: int) -> list[str]:
    """リストを ITEM_LIMIT で切り、残りの数をつける。"""
    if total > len(parts):
        return parts + [f"…ほか {total - len(parts)} 件"]
    return parts
