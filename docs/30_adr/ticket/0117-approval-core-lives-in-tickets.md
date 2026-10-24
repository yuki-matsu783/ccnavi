---
type: adr
title: 承認・取り下げ・レビュー済みの判定のコアと write-and-push の見分けを tickets に置く
description: hook にあった core・core_base・core_approve・core_review・core_withdraw と write-and-push を tickets へ移し、hook には hook の判定だけを残す
tags: [design-doc, ticket]
keywords: [サブパッケージ, tickets, hook, core, core_base, write-and-push, 判定のコア, Chrome 拡張, Pyodide, test_module_layers, ccnavi.tickets.core]
---
# ADR-0117: 承認・取り下げ・レビュー済みの判定のコアと write-and-push の見分けを tickets に置く

状態: 採用

## 状況

ADR-0099 で `src/ccnavi/` を役割ごとのサブパッケージに分けたとき、判定のコア（`core.py` と
`core_base.py`・`core_approve.py`・`core_review.py`・`core_withdraw.py`）と write-and-push の見分け（`write_and_push.py`）は
`hook/` に置いた。名前に「判定」が付き、承認の入口が実行前チェックと同じ深さの段にあったため。

中身は hook の判定ではない。コアが決めるのは、承認の対象・取り下げてよいか・レビュー済みにしてよいかと、
そのとき置き場に書くもので、write-and-push の見分けが決めるのは、親子のチケットが write-and-push の対象かと、置き場の変更を
誰がコミットするか。どちらもチケットの置き場と親子の形だけを読み、hook の payload も実行前チェックの
判定も読まない（`core_base.py` の docstring も、実行前チェックの `judge` と紛れないように
`judge_approval` と名前を変えたと書く）。import も `infra` と `tickets` だけを向いていた。

`hook/` に置いたままだと、承認の仕組みを読みに来た人は `tickets/` で途切れ、`hook/` を開いて初めて
入口に行き着く。`hook/` の説明（hook の判定）とも合わない。

## 決定

| 決めたこと | なぜ | 採らなかった側 |
|---|---|---|
| `hook/core*.py` と `tickets/write_and_push.py` を `tickets/` へ、名前を変えずに移す | 中身はチケットの領域の判定で、読むのも `infra` と `tickets` だけ。名前は `tickets` の既存のモジュール（`approval*`・`review*` など）とぶつからない | `hook/` に残す。役割の線（サブパッケージ）と中身が食い違ったままになる |
| モジュールの段は今のまま（判定と操作の段）にする | `tickets.review`・`tickets.ops` と同じ段を読むので、段は動かない | 段を新しく足す。依存の深さは変わらないので要らない |
| Chrome 拡張の Pyodide の入口は `ccnavi.tickets.core` を引く | 拡張は組み立てのたびに `src/ccnavi/` をサブパッケージの階層ごと同梱するので、入口と同梱の ccnavi は同じ版にそろう | 互換のために `hook/core.py` を残して再輸出する。読み手が 2 つになり、片方だけを直す食い違いを生む |

## 得たもの・失ったもの

- 得たもの: 承認・取り下げ・レビュー済みの入口が、それが動かす置き場と同じ `tickets/` にそろう。
  `hook/` には hook の判定だけが残る
- 失ったもの: `ccnavi.hook.core` を名指ししていた外の読み手（拡張の入口・要件・設計書・テスト）を
  一度に直す必要があった。古い名前で import するものがあれば、読み込みで落ちて気づく

## 採らなかった案

- **名前も変える（`approval_core` など）。** `core.<名前>` で引く呼び手と文書（要件の `core.confirm` など）を
  すべて書き換えることになる。サブパッケージを移すだけで役割の線は合うので、名前は残した
