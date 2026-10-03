"""実行ファイルが自分について言うこと（`ccnavi --version [--json]`）。

言うのは 5 つ。版・組み立ての元になったコミット・受け付けるフラグ・互換の版・読み書きする
書式の版。VS Code 拡張と `.ccnavi/scripts/` の sh は、起動のときにこれを読んで自分と
合っているかを見る。前は拡張が `--flow` を渡してみて、argparse の「知らない
オプション」の苦情で古い実行ファイルを見分けていた。フラグ 1 本ごとに渡してみる形は、
フラグが増えるたびに見分け方も増える。

**互換の版（COMPAT）** は、実行ファイルと、それを呼ぶ側（`.ccnavi/scripts/` の sh と
VS Code 拡張）との契約の版。呼ぶ側が頼っているフラグや出力の形を、呼ぶ側を直さないと
動かない形に変えたときだけ 1 上げ、sh の `CCNAVI_COMPAT`（ccnavi-common.sh）と拡張の
`EXTENSION_COMPAT`（src/core/version.ts）も同じ値に揃える。フラグを足すだけ、JSON の欄を
足すだけなら上げない。足したものが在るかは `flags` を見れば分かる。

層（共通層・自身の層・プロジェクトの層）の 3 本は、ファイルの頭の `version:` に書式の版を
書く。読めない版は、読む側（rules / phasetypes / risk）がもう error にしている
（`--lint` が名指しする）。ここでは実行ファイルが読む版を `formats` に並べるだけで、
層ごとに別の版を足さない。

組み立ての元のコミットは build.py が組み立てのときに `ccnavi_buildinfo` として埋める。
ソースで動かしているときはその部品が無いので `unknown` と言う。実行ファイルは git に
聞かない（ソースで動いているときに作業ツリーの HEAD を言うと、組み立てた版と取り違える）。

ネットワークにも git にも出ない（docs/claude/exe-boundary.md）。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TextIO

from . import phasetypes, risk, rules
from . import ticket as ticket_mod
from .modes import EXIT_OK

# ccnavi の版。pyproject.toml の `version` と揃える（tests/core/test_version.py が見る）。
VERSION = "0.1.0"
# 実行ファイルと sh・拡張の契約の版。上げ方は冒頭の説明のとおり。
COMPAT = 2
# `--version --json` の形の版。欄を足すだけなら上げない。
SCHEMA = 1
# 組み立ての元のコミットが分からないときの綴り。
UNKNOWN = "unknown"
# build.py が組み立てのときに書く部品の名前。パッケージの外に置くので、ソースで動かしている
# ときに前の組み立ての値を読み違えることが無い。
BUILDINFO_MODULE = "ccnavi_buildinfo"


def commit() -> str:
    """組み立ての元になったコミット。埋めていなければ `unknown`。"""
    # 綴りは BUILDINFO_MODULE と同じ。build.py が `build/stamp/` に書き、PyInstaller がまとめる。
    try:
        import ccnavi_buildinfo  # type: ignore[import-not-found]
    except ImportError:
        return UNKNOWN
    value = getattr(ccnavi_buildinfo, "COMMIT", "")
    return value if isinstance(value, str) and value else UNKNOWN


def built() -> bool:
    """PyInstaller で組み立てた実行ファイルとして動いているか。偽ならソースで動いている。"""
    return bool(getattr(sys, "frozen", False))


def flags(parser: argparse.ArgumentParser) -> list[str]:
    """受け付けるフラグ。引数の定義（argparse）から引くので、足したフラグはそのまま並ぶ。"""
    found = {
        option
        for action in parser._actions
        for option in action.option_strings
        if option.startswith("--")
    }
    return sorted(found)


def formats() -> dict[str, int]:
    """読む設定とチケットの書式の版。層のファイルの `version:` と比べるもの。"""
    return {
        "phases": phasetypes.VERSION,
        "risks": risk.VERSION,
        "rules": rules.VERSION,
        "ticket": ticket_mod.VERSION,
    }


def describe(parser: argparse.ArgumentParser) -> dict[str, object]:
    """`--version --json` の中身。"""
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "commit": commit(),
        "built": built(),
        "compat": COMPAT,
        "flags": flags(parser),
        "formats": formats(),
    }


def report(stdout: TextIO, parser: argparse.ArgumentParser, as_json: bool) -> int:
    """版を書く。ユーザ向けの形も 1 行 1 項目の `<名前>: <値>` にして、
    sh が sed で読めるようにする。"""
    body = describe(parser)
    if as_json:
        stdout.write(json.dumps(body, ensure_ascii=True, indent=1))
        stdout.write("\n")
        return EXIT_OK
    how = "組み立てた実行ファイル" if body["built"] else "ソースで動いている"
    stdout.write(f"ccnavi {body['version']}\n")
    stdout.write(f"commit: {body['commit']}\n")
    stdout.write(f"built: {'yes' if body['built'] else 'no'} ({how})\n")
    stdout.write(f"compat: {body['compat']}\n")
    stdout.write(f"schema: {body['schema']}\n")
    stdout.write(f"formats: {_pairs(body['formats'])}\n")
    stdout.write(f"flags: {' '.join(body['flags'])}\n")
    return EXIT_OK


def _pairs(values) -> str:
    return " ".join(f"{name}={value}" for name, value in sorted(values.items()))
