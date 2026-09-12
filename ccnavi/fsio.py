"""ファイルの読み書きの型。控え、印、写し、下書きが全部これを通る。

読めなければ None、書けなければ理由の文、という形に揃えてある。hook の中で
読み書きの失敗が例外のまま上へ抜けると、判定に達しないまま終わる。ここで
受け止めて値にしておけば、呼ぶ側は「読めなかったときにどうするか」だけを
書けばよく、try が 10 か所に散らばらない。

理由の文には例外の文字列だけを入れる。「書けない」「印を置けない」のような
言い回しは呼ぶ側の用件で、ここでは決めない。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
from typing import Any

# ファイル名に混ぜられない字。セッションやエージェントの識別子をそのまま名前に
# するので、区切り文字が混じった値でファイルを別の場所へ書かせない。
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def stamp() -> str:
    """印と記録に書く時刻。手元の時計、オフセット付き。"""
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def slashed(path: str) -> str:
    """区切りを "/" に揃える。ルールと範囲の glob は "/" で書かれている。"""
    return path.replace("\\", "/")


def safe_name(text: str, limit: int | None = 64) -> str:
    """識別子から作る、置き場の名前。limit が None なら切り詰めない。"""
    return _UNSAFE.sub("_", text)[:limit]


def read_text(path: str, errors: str = "strict") -> str | None:
    """本文。読めなければ None。"""
    try:
        with open(path, encoding="utf-8", errors=errors) as f:
            return f.read()
    except OSError:
        return None


def read_bytes(path: str) -> bytes | None:
    """中身をそのまま読む。読めなければ None。

    バイト列で扱う。改行を変換すると、控えから戻したファイルが元と 1 バイト
    違うものになる。Windows と Linux で同じ控えを取るために、ここは解釈しない。
    """
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def write_text(path: str, text: str, newline: str | None = None) -> str:
    """本文を書く。親ディレクトリが無ければ作る。書けたら空文字、駄目なら理由。"""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8", newline=newline) as f:
            f.write(text)
    except OSError as exc:
        return f"{exc}"
    return ""


def write_bytes(path: str, content: bytes) -> str:
    """中身をそのまま書く。書けたら空文字、駄目なら理由。"""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)
    except OSError as exc:
        return f"{exc}"
    return ""


def read_json(path: str) -> tuple[Any, Exception | None]:
    """JSON を読む。読めなければ (None, 例外)。

    例外を返すのは、呼ぶ側が「無い」と「壊れている」を分けるため。無いのは
    普通の状態で黙ってよいが、壊れているのは言わないと直らない。
    """
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), None
    except (OSError, ValueError) as exc:
        return None, exc


def read_dict(path: str) -> dict | None:
    """JSON の辞書。読めなければ None、読めたが辞書でなければ空の辞書。"""
    data, failed = read_json(path)
    if failed is not None:
        return None
    return data if isinstance(data, dict) else {}


def write_json(path: str, data: Any, indent: int | None = None) -> str:
    """JSON を書く。書けたら空文字、駄目なら理由。"""
    return write_text(path, json.dumps(data, ensure_ascii=False, indent=indent))


def remove(path: str) -> None:
    """消す。無くても、消せなくても黙る。"""
    with contextlib.suppress(OSError):
        os.remove(path)
