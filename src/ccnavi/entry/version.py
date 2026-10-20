"""実行ファイルが自分について言うこと（`ccnavi --version [--json]`）。

言うのは 6 つ。版・組み立ての元になったコミット・受け付けるフラグ・互換の版・読み書きする
書式の版・実行ファイルが持つ振る舞い（`features`）。VS Code 拡張と `.ccnavi/scripts/` の sh は、
起動のときにこれを読んで自分と合っているかを見る。受け付けるフラグから読めない振る舞い
（JSON の欄の有無など）は `features` で言う。前は拡張が `--flow` を渡してみて、argparse の「知らない
オプション」の苦情で古い実行ファイルを見分けていた。フラグ 1 本ごとに渡してみる形は、
フラグが増えるたびに見分け方も増える。

互換の版（COMPAT）は、実行ファイルと、それを呼ぶ側（`.ccnavi/scripts/` の sh と
VS Code 拡張）との契約の版。呼ぶ側が頼っているフラグや出力の形を、呼ぶ側を直さないと
動かない形に変えたときに 1 上げ、sh の `CCNAVI_COMPAT`（ccnavi-common.sh）と拡張の
`EXTENSION_COMPAT`（extensions/vscode/ccnavi-board/src/core/version.ts）も同じ値に揃える。
Chrome 拡張は組み立てのときにここの値を埋め込む。

データの形（承認済みの置き場に置くものの並び、待ち方の読み方、取り下げの条件など）が変わるときも
上げる。古い実行ファイル（古いコアを積んだ Chrome 拡張を含む）が新しい形のデータを読むと、
フラグが同じでも読み違える（計画の項の `after` を知らない実行ファイルは待ち方を一直線と読む）。
sh が古い実行ファイルの知らないサブコマンドを呼ぶようになったときも同じ。

フラグを足すだけ、JSON の欄を足すだけで、データの形も変わらないなら上げない。足したものが
在るかは `flags` を見れば分かる。

レイヤー（共通レイヤー・自身のレイヤー・プロジェクトのレイヤー）の 3 本は、
ファイルの頭の `version:` に書式の版を
書く。読めない版は、読む側（rules / phasetypes / risk）がもう error にしている
（`--lint` が名指しする）。ここでは実行ファイルが読む版を `formats` に並べるだけで、
レイヤーごとに別の版を足さない。

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

from ..infra.modes import EXIT_OK
from ..policy import rules
from ..tickets import phasetypes, risk, ticket_model

# ccnavi の版。pyproject.toml の `version` と揃える（tests/core/test_version.py が見る）。
VERSION = "0.1.0"
# 実行ファイルと sh・拡張の契約の版。上げ方は冒頭の説明のとおり。
# 13: 新しい sh が実行ファイルの新しいサブコマンド（`tree parent` の追加行、`sync retired`）を呼ぶ。
# Chrome と手元の判定の規則が変わる（取り込み状態を読まない）。
# 14: C1 を write-and-push に改名した。sh が呼ぶサブコマンドが
# `c1 family|sort|merge|merge-blame` から `write-and-push …` に変わり、答えの頭の行も
# `c1 1` から `write-and-push 1` に変わった。
# 15: `integrate` がチケットに結び付くかの判定を、実行ファイルの `tree bound` に聞くようにした。
# sh が呼ぶサブコマンドが増え、sh は答えの頭の行 `tree 1` が無ければ断る向きに倒す。
# 16: ユーザだけが打つ操作を `user <操作>`（agree・reviewed・close-early・prune・handoff）に集め、
# sh を ccnavi-user-*.sh に分けた。`user` の外の形は断るので、古い sh は新しい実行ファイルで
# 動かない。
# 17: 送信を操作記録（`ops` サブコマンド）に載せ、decide の判断のダイジェストを版 2 にした。
# 手で動かした子の確定が受け入れ台帳（`phases/<親>/accepted.json`）と `via: manual` を書く。
# 新しい sh が実行ファイルの `ops …` を呼び、ボードは版 1 の decide を断る。旧い sh は新しい
# 実行ファイルの decide（版 2）を扱えず、旧い実行ファイルは新しい sh の `ops` を知らない。
COMPAT = 17
# `--version --json` の形の版。欄を足すだけなら上げない。
SCHEMA = 1
# 実行ファイルが持つ振る舞い。オプションや JSON の欄の有無を `flags` から読めないものを足す。
# 足すだけなら COMPAT は上げない。
FEATURES: tuple[str, ...] = (
    "templates.draft-text",
    "user-entry",
    # 保護対象の下書きの取り込み（`ccnavi user handoff`）。テンプレート専用の取り込み・編集
    # （前の `templates.edit`・`board.template-draft`）の代わり。
    "handoff",
    # 直接作業の取り込み先がチケットに結び付くかの問い合わせ（`tree base-bound`）。
    "tree.base-bound",
    # 取り込みが自己防衛の対象を含むかと、取り込んでいない下書きの問い合わせ。
    "tree.guarded",
    "tree.handoff-pending",
)
# 組み立ての元のコミットが分からないときの表記。
UNKNOWN = "unknown"
# build.py が組み立てのときに書く部品の名前。パッケージの外に置くので、ソースで動かしている
# ときに前の組み立ての値を読み違えることが無い。
BUILDINFO_MODULE = "ccnavi_buildinfo"


def commit() -> str:
    """組み立ての元になったコミット。埋めていなければ `unknown`。"""
    # 名前は BUILDINFO_MODULE と同じ。build.py が `build/stamp/` に書き、PyInstaller がまとめる。
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
    """読む設定とチケットの書式の版。レイヤーのファイルの `version:` と比べるもの。"""
    return {
        "phases": phasetypes.VERSION,
        "risks": risk.VERSION,
        "rules": rules.VERSION,
        "ticket": ticket_model.VERSION,
    }


def describe(parser: argparse.ArgumentParser) -> dict[str, object]:
    """`--version --json` の中身。"""
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "commit": commit(),
        "built": built(),
        "compat": COMPAT,
        "features": list(FEATURES),
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
    stdout.write(f"features: {' '.join(body['features'])}\n")
    stdout.write(f"flags: {' '.join(body['flags'])}\n")
    return EXIT_OK


def _pairs(values) -> str:
    return " ".join(f"{name}={value}" for name, value in sorted(values.items()))
