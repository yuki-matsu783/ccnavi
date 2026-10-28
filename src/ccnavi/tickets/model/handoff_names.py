"""下書きの置き場（`wip/handoff/`）での階層名の読み替え。

Claude Code 本体は、`.claude` という名前の階層（深さを問わない）を機密パスとして扱い、
`--allowedTools Edit` を渡しても書き込みを断る。非対話（`claude -p`）では許可できないので、
`wip/handoff/.claude/settings.json` のような下書きは編集できない。そこで、下書きの相対パスの中の
その階層を、表（`ALIASES`）で別の名前に読み替えて置き、取り込むときに戻す。

    .claude/settings.json                   wip/handoff/handoff-.claude/settings.json
    projects/<名前>/.claude/hooks/x.sh      wip/handoff/projects/<名前>/handoff-.claude/hooks/x.sh

## 表の持ち方

表は `.ccnavi/scripts/ccnavi-handoff-names.txt` の 1 か所だけにある。`ccnavi-stage.sh`（sh）がそれを
読み、この実行ファイル（Python）も同じファイルを読む。`ccnavi-stage.sh` は実行ファイルが無くても動く
作りなので、sh が実行ファイルに聞く形は採らなかった。実行ファイルには、`build.py` が組み立てるときに
同じファイルを同梱する。

## 比べ方

表の名前は ASCII だけで書き、要素ごとに大文字小文字を区別せずに比べる（`.CLAUDE` は `.claude`、
`Handoff-.Claude` は `handoff-.claude`）。非 ASCII を含む要素は、`fold` で一致しても表に当てない。
sh は ASCII の大文字小文字しか畳めない。両方が同じ答えを返すよう、
Python も非 ASCII の要素には表を当てない。

## 断る形

- 元のパスに、下書きでの階層名（予約名）の要素がある（`reserved_problem`）。読み替えると、ほかの
  元のパスと区別できなくなるので、`ccnavi-stage.sh` が断る

読み替える前の形（`wip/handoff/.claude/…`）の下書きは特別に扱わない。表の下書きでの名前を含まない
ふつうのパスとして、そのまま元のパスと読む。
"""

from __future__ import annotations

import os
import sys

from ...infra import pathcase

# 表のファイル名。`.ccnavi/scripts/` に置き、sh と実行ファイルが同じものを読む。
NAMES_FILE = "ccnavi-handoff-names.txt"


def _names_path() -> str:
    """表のファイルの場所。

    組み立てた実行ファイルでは同梱の写し（PyInstaller の展開先）、Chrome 拡張の Python では
    パッケージの直下の写し（`python.js` が入れる）、ソースではリポジトリの `.ccnavi/scripts/`。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    package = os.path.dirname(os.path.dirname(here))
    repo = os.path.dirname(os.path.dirname(package))
    candidates = [
        os.path.join(getattr(sys, "_MEIPASS", ""), NAMES_FILE),
        os.path.join(package, NAMES_FILE),
        os.path.join(repo, ".ccnavi", "scripts", NAMES_FILE),
    ]
    return next((c for c in candidates if os.path.isfile(c)), candidates[-1])


def parse(text: str) -> dict[str, str]:
    """表のファイルの中身を、元の階層名 → 下書きでの階層名の辞書にする。

    `#` で始まる行と空行は読まない。1 行は空白で区切った 2 語だけ。読めない行は例外にする。
    黙って捨てると、読み替えられない名前のまま下書きが置かれる。
    """
    table: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        words = line.split()
        if len(words) != 2 or not all(w.isascii() and "/" not in w for w in words):
            raise ValueError(f"{NAMES_FILE} の {number} 行目を読めない: {raw!r}")
        table[words[0]] = words[1]
    return table


def load(path: str | None = None) -> dict[str, str]:
    with open(path or _names_path(), encoding="utf-8") as f:
        return parse(f.read())


# 元の階層名 → 下書きでの階層名。関数は呼ぶたびにこの辞書を参照するので、
# テストは中身を差し替えられる。
ALIASES: dict[str, str] = load()


def _same(name: str, other: str) -> bool:
    return name.isascii() and pathcase.fold(name) == pathcase.fold(other)


def _convert(rel: str, pairs: list[tuple[str, str]]) -> str:
    parts = rel.split("/")
    for i, part in enumerate(parts):
        for before, after in pairs:
            if _same(part, before):
                parts[i] = after
                break
    return "/".join(parts)


def to_draft(rel: str) -> str:
    """元の相対パス（"/" 区切り）を、下書きの置き場の中の相対パスにする。"""
    return _convert(rel, list(ALIASES.items()))


def from_draft(rel: str) -> str:
    """下書きの置き場の中の相対パスを、元の相対パスにする。"""
    return _convert(rel, [(after, before) for before, after in ALIASES.items()])


def reserved_problem(rel: str) -> str:
    """元の相対パスが予約名の階層を含むなら、断る理由。含まなければ空。"""
    for part in rel.split("/"):
        for after in ALIASES.values():
            if _same(part, after):
                return (
                    f"{rel} の `{part}` は、下書きの置き場で `{_original_of(after)}` を読み替える"
                    "予約した名前なので、この名前の階層は下書きにできない"
                )
    return ""


def _original_of(after: str) -> str:
    for before, alias in ALIASES.items():
        if alias == after:
            return before
    return after
