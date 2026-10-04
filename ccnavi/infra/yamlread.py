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
純 Python の読み手は、数百段を超える入れ子で `RecursionError` を出す。これは下の
「例外とメッセージ」のとおり `LoadError` に包んで上げる。
深さを数える分の手間は C で組み立てる手間の半分ほどで、純 Python で読むより十分速い。

## 二つの読み手で結果が分かれる文書は純 Python で読む

libyaml と純 Python の読み手は、次の文書で読める・読めないや組み立てる値が分かれる。

- タブ。純 Python の読み手は行末・`:` の後ろ・クォートの後ろなどのタブを `ScannerError` で
  断るが、libyaml は受け入れる
- 先頭以外の BOM（`U+FEFF`）。値やエラーが変わる
- 非特定タグ `!` 単体（`key: !`）。純 Python は None、libyaml は空文字列を作る
- ブロックスカラーの頭の直後の `#`（`key: |#`）。純 Python は断り、libyaml は空文字列にする
- 行頭の `%` で始まる知らない指示子（`%foo`）。純 Python は読み飛ばし、libyaml は断る
- フローの中で `:` の直後に区切りが来る形（`{a:}`・`[a:]`）と、`?` 単体の明示のキー
  （`[? ]`）。純 Python は読み、libyaml は断る

C を使うのは速さのためで、判定は変えない。そこで、文書にこれらの書き方があれば純 Python の
読み手に回す（`_SPLIT`）。どれも文字を見るだけの粗い当て方で、分かれない文書も回すことがある
（BOM は先頭のものも、`?` は文字列の中のものも回す）が、遅くなるだけで結果は変わらない。
実際のルール・設定・チケットにはまず出ないので、速さはほぼ変わらない。この一覧は、ランダムに
組んだ短い文書で二つの読み手を見比べて拾ったもの。

## 例外とメッセージ

構文の誤りは、どちらの読み手でも `yaml.YAMLError` の子（`ScannerError`・`ParserError` など）で
上がる。C の読み手のメッセージは位置（行・桁）までで、純 Python の読み手が添える該当行の
抜き出し（`^` の印）は付かない。

値を組み立てる途中の失敗は、PyYAML が `yaml.YAMLError` に包まずに素の例外で上げる。
`!!int`（空）や `!!int abc` の `IndexError`・`ValueError`、`!!bool` に知らない語を書いた
`KeyError`、ありえない日付の `!!timestamp` の `ValueError`、深すぎる入れ子の `RecursionError`、
孤立したサロゲートを含む文字列の `UnicodeEncodeError` などである。呼び手は `yaml.YAMLError`
しか捕まえないので、素のまま上げると hook が非 0 で終わり、Claude Code は判定を読まない
（止めるはずの呼び出しが通る）。そこでこれらは `LoadError`（`yaml.YAMLError` の子）に包んで
`raise ... from` で上げ、呼び手からは構文の誤りと同じ「読めない文書」に見せる。
"""

from __future__ import annotations

import re

import yaml

# libyaml があれば C の読み手。無ければ純 Python の読み手。
LOADER: type = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

# C の読み手で組み立ててよい入れ子の深さ。純 Python の読み手が `RecursionError` を出す深さ
# （300 段から 500 段の間）より十分浅く、Windows の 1 MB のスタックでも余裕がある値にする。
DEPTH_LIMIT = 100

# 二つの読み手で結果が分かれうる書き方。当たれば純 Python で読む（上の docstring の一覧と同じ順）。
_SPLIT = re.compile(
    r"[\t\ufeff]"  # タブ・BOM
    r"|!(?=[\s,\]}]|\Z)"  # 非特定タグ `!` 単体
    r"|[|>][0-9+-]*#"  # ブロックスカラーの頭の直後の `#`
    r"|(?:\A|[\r\n\x85\u2028\u2029])%"  # 行頭の `%`（指示子）
    r"|:[,\]}]"  # `:` の直後のフローの区切り
    r"|\?(?=[\s,\]}]|\Z)"  # `?` 単体（明示のキー）
)

# 値を組み立てる途中に PyYAML が素のまま上げる例外。`LoadError` に包む。
# UnicodeError（UnicodeEncodeError の親）は ValueError の子なので ValueError で拾える。
_WRAPPED = (
    ValueError,
    IndexError,
    KeyError,
    AttributeError,
    TypeError,
    OverflowError,
    RecursionError,
)

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


class LoadError(yaml.YAMLError):
    """値を組み立てる途中の失敗（`ValueError`・`RecursionError` など）を包んだもの。"""


def load(text: str, loader: type) -> object:
    """`yaml.load(text, Loader=loader)`。組み立ての途中の素の例外は `LoadError` に包む。

    `loader` は safe な読み手（`SafeLoader`・`CSafeLoader` か、それに制限を足した子）に限る。
    yamlread を通さずに読む呼び手（別名を拒む `flow._Loader` など）もこれを使う。
    """
    try:
        return yaml.load(text, Loader=loader)  # noqa: S506  safe な読み手に限る（上）
    except yaml.YAMLError:
        raise
    except RecursionError as exc:
        raise LoadError("入れ子が深すぎる") from exc
    except _WRAPPED as exc:
        raise LoadError(f"値を組み立てられない ({type(exc).__name__}: {exc})") from exc


def safe_load(text: str, loader: type | None = None) -> object:
    """`yaml.safe_load(text)` と同じものを返す。読み手は `LOADER`（C があれば C）。

    読めない文書は、構文の誤りも組み立ての途中の失敗も `yaml.YAMLError`（の子）で上げる。
    `loader` はテストが読み手を差し替えるためのもの。渡すのは `CSafeLoader` か `SafeLoader` に限る。
    """
    loader = loader or LOADER
    if loader is not yaml.SafeLoader:
        try:
            if _SPLIT.search(text) or not _shallow(text, loader):
                loader = yaml.SafeLoader
        except yaml.YAMLError:
            raise
        except _WRAPPED as exc:  # 孤立したサロゲートで C のパーサが UnicodeEncodeError を出すなど
            raise LoadError(f"読めない ({type(exc).__name__}: {exc})") from exc
    return load(text, loader)
