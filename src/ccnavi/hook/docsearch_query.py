"""索引の引き方。`--docs` の問いの検査、当たりの判定、並べ方と表の描き方。

docsearch から分けた。docsearch を読まない。
"""

from __future__ import annotations

import datetime
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, TextIO

from . import docsearch_index

SORTS = ("path", "mtime", "type", "title")
FORMATS = ("table", "path", "detail", "json", "jsonl", "count")
# `--since` / `--until` に受ける形と、その形の読み方。書き誤った値が気づかないうちに
# 0 件になるのを避ける。
_WHEN_FORMATS = {
    10: "%Y-%m-%d",
    13: "%Y-%m-%dT%H",
    16: "%Y-%m-%dT%H:%M",
    19: "%Y-%m-%dT%H:%M:%S",
}
_WHEN = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}(:\d{2}(:\d{2})?)?)?$")


# --- 引く ---------------------------------------------------------------------------


@dataclass
class Query:
    types: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    since: str = ""
    until: str = ""
    sort: str = "path"
    reverse: bool = False
    limit: int = 0
    format: str = "table"


def _valid_when(value: str) -> bool:
    if not _WHEN.match(value):
        return False
    try:
        datetime.datetime.strptime(value, _WHEN_FORMATS[len(value)])
    except (KeyError, ValueError):
        return False
    return True


def problems_of(query: Query) -> list[str]:
    """使い方の誤り。空なら引ける。"""
    found = []
    if query.sort not in SORTS:
        found.append(f"--sort は {' / '.join(SORTS)} のどれか（{query.sort!r} は使えない）")
    if query.format not in FORMATS:
        found.append(f"--format は {' / '.join(FORMATS)} のどれか（{query.format!r} は使えない）")
    for flag, value in (("--since", query.since), ("--until", query.until)):
        if value and not _valid_when(value):
            found.append(
                f"{flag} には日時を YYYY-MM-DD[THH[:MM[:SS]]] の形で書く（{value!r} は読めない）"
            )
    return found


def _fm(row: dict) -> dict:
    front = row.get("frontmatter")
    return front if isinstance(front, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        try:
            return docsearch_index._dumps(value)
        except (ValueError, RecursionError):
            return ""
    try:
        return str(value)
    except ValueError:  # 桁の多すぎる整数
        return ""


def _str(row: dict, key: str) -> str:
    return _text(_fm(row).get(key))


def _arr(row: dict, key: str) -> list[str]:
    value = _fm(row).get(key)
    if value is None:
        return []
    if isinstance(value, list):
        return [_text(v) for v in value]
    return [_text(value)]


def _scalars(value: Any) -> list[str]:
    """入れ子のスカラーの値。再帰しない（深い入れ子で失敗しないように）。"""
    found = []
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            stack.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            stack.extend(reversed(item))
        elif item is not None:
            found.append(_text(item))
    return found


def _fold(text: str) -> str:
    """比べる形。NFC に揃えて小文字にする。"""
    return docsearch_index._norm(text).lower()


def _searchable(row: dict) -> str:
    parts = [_text(row.get("concept_id")), _text(row.get("mtime")), *_scalars(_fm(row))]
    return _fold("\n".join(parts))


def _needles(values: list[str]) -> list[str]:
    return [_fold(v) for v in values if v]


def _until_end(until: str) -> str:
    """`--until` を、書いた桁の終わりまで延ばす。

    mtime は `YYYY-MM-DDTHH:MM:SS` で、文字列で比べる。`2026-08-05` のままだと
    `2026-08-05T00:00:00` より小さく、その日に書いたものが 1 本も残らない。
    """
    tail = {10: "T23:59:59", 13: ":59:59", 16: ":59"}.get(len(until), "")
    return until + tail if until else until


def matches(row: dict, query: Query) -> bool:
    def exact(needles: list[str], hay: list[str]) -> bool:
        if not needles:
            return True
        folded = {_fold(h) for h in hay}
        return any(n in folded for n in needles)

    def partial(needles: list[str], hay: str) -> bool:
        return not needles or any(n in hay for n in needles)

    mtime = _text(row.get("mtime"))
    until = _until_end(query.until)
    return (
        exact(_needles(query.types), [_str(row, "type")])
        and exact(_needles(query.tags), _arr(row, "tags"))
        and exact(_needles(query.keywords), _arr(row, "keywords"))
        and partial(_needles(query.paths), _fold(_text(row.get("concept_id"))))
        and partial(_needles(query.texts), _searchable(row) if query.texts else "")
        and (not query.since or mtime >= query.since)
        and (not until or mtime <= until)
    )


def _sort_key(query: Query):
    def key(row: dict):
        cid = docsearch_index._norm(_text(row.get("concept_id")))
        if query.sort == "mtime":
            return (_text(row.get("mtime")), cid)
        if query.sort in ("type", "title"):
            return (_fold(_str(row, query.sort)), cid)
        return (cid,)

    return key


def _char_width(c: str) -> int:
    if unicodedata.combining(c) or unicodedata.category(c) in ("Mn", "Me", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1


def dwidth(text: str) -> int:
    """見た目の幅。東アジアの全角（W / F）を 2、結合文字と書式文字を 0 と数える。"""
    return sum(_char_width(c) for c in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - dwidth(text))


def _one_line(text: str) -> str:
    """表と詳しい形に出す値を 1 行にまとめる（改行で行が分かれないように）。"""
    return " ".join(text.split()) if ("\n" in text or "\r" in text) else text


def search(rows: list[dict], query: Query) -> tuple[list[dict], int]:
    """(出す行, 絞った後で --limit で切る前の数)。"""
    hits = sorted((r for r in rows if matches(r, query)), key=_sort_key(query))
    if query.reverse:
        hits.reverse()
    matched = len(hits)
    if query.limit > 0:
        hits = hits[: query.limit]
    return hits, matched


def render(out: TextIO, hits: list[dict], matched: int, total: int, fmt: str) -> None:
    if fmt == "count":
        shown = f" shown={len(hits)}" if len(hits) != matched else ""
        out.write(f"matched={matched}{shown} total={total}\n")
    elif fmt == "path":
        for row in hits:
            out.write(_text(row.get("concept_id")) + "\n")
    elif fmt == "jsonl":
        for row in hits:
            out.write(docsearch_index._dumps(row) + "\n")
    elif fmt == "json":
        out.write(json.dumps(hits, ensure_ascii=False, indent=2) + "\n")
    elif fmt == "detail":
        for row in hits:
            out.write(
                f"- {_text(row.get('concept_id'))}\n"
                f"  type       : {_one_line(_str(row, 'type'))}\n"
                f"  title      : {_one_line(_str(row, 'title'))}\n"
                f"  description: {_one_line(_str(row, 'description'))}\n"
                f"  tags       : {_one_line(', '.join(_arr(row, 'tags')))}\n"
                f"  keywords   : {_one_line(', '.join(_arr(row, 'keywords')))}\n"
                f"  mtime      : {_text(row.get('mtime'))}\n"
            )
    else:
        types = [_one_line(_str(r, "type")) for r in hits]
        ids = [_text(r.get("concept_id")) for r in hits]
        tw = max((dwidth(t) for t in types), default=0)
        cw = max((dwidth(c) for c in ids), default=0)
        for row, kind, cid in zip(hits, types, ids, strict=True):
            line = f"{_pad(kind, tw)}  {_pad(cid, cw)}  {_one_line(_str(row, 'title'))}"
            out.write(line.rstrip() + "\n")
