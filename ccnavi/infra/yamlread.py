"""YAML を安全な読み手で読む。libyaml（C）があればそれを使い、無ければ純 Python の読み手で読む。

## 読み手は safe に限る

使うのは `yaml.CSafeLoader` か `yaml.SafeLoader` だけ。どちらも `safe_load` と同じ型しか作らない。
任意の Python の型を組み立てる読み手は、エージェントが書けるファイル（チケット・ルール・設定）に
向けては使わない。

## C の読み手を使う理由

hook はツール呼び出しのたびに走り、そのたびにチケットの frontmatter とルール・設定を読み直す。
純 Python の読み手は、同じ文書を読むのに C の読み手のおよそ 10 倍かかる。チケットの本数に
比例して hook が遅くなる。

PyYAML を libyaml 無しで入れた環境（wheel の無い機械など）では `CSafeLoader` が無い。
そのときは `SafeLoader` に戻る。読める文書と組み立てる値は同じ。

## 入れ子が深い文書は純 Python で読む

C の読み手は、ノードを組み立てる部分が C のスタックで再帰する。入れ子が数万段ある文書
（`[[[[...]]]]`。数十 KB で作れる）を渡すと、Python の例外にならずにプロセスごと落ちる
（Linux で SIGSEGV）。hook が落ちると判定が返らず、止めるはずの呼び出しが通る。Windows は
既定のスタックが小さいので、もっと浅い入れ子で落ちる。

そこで、組み立てる前に C のパーサでイベントだけを流して入れ子の深さを数え、`DEPTH_LIMIT` を
超えたら純 Python の読み手に回す。イベントを流す部分は再帰しないので深くても落ちない。
純 Python の読み手は深い入れ子を `RecursionError` で断るので、以前と同じ結果になる。
深さを数える分の手間は C で組み立てる手間の半分ほどで、純 Python で読むより十分速い。

## 例外とメッセージ

構文の誤りは、どちらの読み手でも `yaml.YAMLError` の子（`ScannerError`・`ParserError` など）で
上がる。C の読み手のメッセージは位置（行・桁）までで、純 Python の読み手が添える該当行の
抜き出し（`^` の印）は付かない。
"""

from __future__ import annotations

import yaml

# libyaml があれば C の読み手。無ければ純 Python の読み手。
LOADER: type = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

# C の読み手で組み立ててよい入れ子の深さ。純 Python の読み手が `RecursionError` で断る深さ
# （300 段から 500 段の間）より十分浅く、Windows の 1 MB のスタックでも余裕がある値にする。
DEPTH_LIMIT = 100

_OPEN = (yaml.SequenceStartEvent, yaml.MappingStartEvent)
_CLOSE = (yaml.SequenceEndEvent, yaml.MappingEndEvent)


def _shallow(text: str, loader: type) -> bool:
    """入れ子が `DEPTH_LIMIT` 段以内か。構文の誤りはここで `yaml.YAMLError` として上がる。"""
    depth = 0
    for event in yaml.parse(text, Loader=loader):  # noqa: S506  イベントを流すだけで値は作らない
        if isinstance(event, _OPEN):
            depth += 1
            if depth > DEPTH_LIMIT:
                return False
        elif isinstance(event, _CLOSE):
            depth -= 1
    return True


def safe_load(text: str, loader: type | None = None) -> object:
    """`yaml.safe_load(text)` と同じものを返す。読み手は `LOADER`（C があれば C）。

    `loader` はテストが読み手を差し替えるためのもの。渡すのは `CSafeLoader` か `SafeLoader` に限る。
    """
    loader = loader or LOADER
    if loader is not yaml.SafeLoader and not _shallow(text, loader):
        loader = yaml.SafeLoader
    return yaml.load(text, Loader=loader)  # noqa: S506  LOADER は CSafeLoader か SafeLoader
