"""判定を実行せずに試す。ユーザがターミナルから打つ経路。

## なぜ要るか

ルールを 1 件書いたとき、それが何にヒットするのかは走らせるまで分からない。
`glob` は正規表現に翻訳されるし、Bash のコマンドは実行される部分まで
絞られてからヒットする。書いたユーザの頭の中のヒットの仕方と、実際のヒットの仕方が食い違っても、
食い違ったことに気づく手立てが無かった。食い違いに気づかれないルールは、足したつもりで
何も止めていない 1 行になる。

## 判定と同じ経路を通る

ここは判定を作り直さない。payload を組み立てて `judge.decide_before` を
そのまま呼び、返った応答と記録を読んでユーザに見せる。別の経路で判定すると、
試験で通ったものが実運用で失敗する、という一番まずい形になる。

モードは常に enable で動かす。試験は「止まるかどうか」を問うものなので、
呼び出しに手を出さないモードの結果を見せても答えになっていない。
実際に走っているモードが dry-run の場合でも、ここは enable の判定を返す。

## 分け方

このモジュールは名前を引き受けるだけで、中身は持たない。呼び出し側（cli・suggest）は
`diagnose.test` のように引き続きここから呼ぶ。

* `diagnose_try`: `--test` と `--test-expectations`。1 件の試験と期待する判定の回し方
* `diagnose_explain`: `--explain` の本文
* `diagnose_board`: ボードの中身（`--explain --json`）
* `diagnose_shared`: explain・board・try が共有するレイヤーの読み出しとルールの書き方

テストでの patch は実体のモジュール（`diagnose_try` など）に対して行う。
ファサードの名前を差し替えても、モジュール内部の呼び出しには効かない。
効くのは、呼び手が `diagnose.<名前>` を属性参照で引く場合だけ。
"""

from __future__ import annotations

from . import diagnose_board, diagnose_explain, diagnose_try

BOARD_VERSION = diagnose_board.BOARD_VERSION
board = diagnose_board.board

explain = diagnose_explain.explain
explain_json = diagnose_explain.explain_json

KNOWN_TOOLS = diagnose_try.KNOWN_TOOLS
EXPECTATION_PLACEHOLDER = diagnose_try.EXPECTATION_PLACEHOLDER
TEST_VERSION = diagnose_try.TEST_VERSION
load_expectations = diagnose_try.load_expectations
run_expectations = diagnose_try.run_expectations
test = diagnose_try.test
test_json = diagnose_try.test_json
test_expectations = diagnose_try.test_expectations
try_one = diagnose_try.try_one
Call = diagnose_try.Call
