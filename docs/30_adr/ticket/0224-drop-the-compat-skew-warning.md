---
type: adr
title: sh と実行ファイルは必ず同時に配る前提にし、互換の版の食い違いを知らせる処理をやめる
description: sh の ccnavi_compat_skew・tell_skew と、VS Code 拡張が起動時に出す互換の版の食い違いの通知を消す。互換の版そのものの宣言（COMPAT・CCNAVI_COMPAT・EXTENSION_COMPAT）、--version の出力、--lint の突き合わせ、配布の組み立ての確かめは残す
tags: [ticket, sh-scripts, distribution]
keywords: [互換の版, COMPAT, CCNAVI_COMPAT, EXTENSION_COMPAT, 食い違い, 同時に配る, 後方互換, ccnavi_compat_skew, tell_skew, 組み立て直し, 配り直し]
---
# ADR-0224: sh と実行ファイルは必ず同時に配る前提にし、互換の版の食い違いを知らせる処理をやめる

状態: 採用

## 状況

ccnavi は sh（`.ccnavi/scripts/`）と実行ファイルの 2 つで動き、両者に互換の版がある。sh は実行ファイルを起こす前に `--version` を読んで自分の `CCNAVI_COMPAT` と比べ（`ccnavi_compat_skew`、レビューの sh では `tell_skew`）、食い違えば標準エラーに直し方を 1 行出して先へ進んでいた。VS Code 拡張も起動のときに 1 度同じ比べをして通知を出していた。呼び出しは 9 本の sh の 13 か所（`ccnavi_compat_skew` 6、`tell_skew` 7）に散らばり、`ready` には食い違いのときだけ出す別の案内もあった。

どれも止めずに知らせるだけの処理だった。一方で配布は、sh と実行ファイルを同じ版で一緒に配る形に決まっている。食い違いは、配り方を誤った人だけが通る異常状態である。

## 決定

| 決めたこと | なぜ | 採らなかった側 |
|---|---|---|
| sh と実行ファイルは必ず同時に配る前提にする。`ccnavi_compat_skew`・`tell_skew` と、全ての呼び出し・文面を消す | 後方互換は考えない決まり。まれな異常状態のために、全ての操作の入口で毎回 `--version` を起こして比べる処理を持たない | 全ての呼び出しを残す。呼び出しを 1 本の関数にまとめて残す |
| VS Code 拡張の起動時の食い違いの通知（`skewMessage`・`warnVersionSkew`）を消す | 同じ前提（拡張は同じ版の実行ファイルと一緒に配る） | 通知だけ残す |
| 互換の版の宣言（`COMPAT`・`CCNAVI_COMPAT`・`EXTENSION_COMPAT`）、`--version` の `compat`、`--lint` の `(version)` の warn は残す | Chrome 拡張は同梱の実行ファイルと統合先の sh の版を、書く操作の前に比べて断る（別の配布経路で、同時に配れない）。`--lint` と配布の組み立ては、食い違いを見つけるための報告 | 宣言ごと消す |
| 拡張が `flags`・`features` を見て、無い機能の画面を開かない処理（`missingFlags`・`missingFeatures`）は残す（ADR-0226 で覆した） | 食い違いの通知ではなく、使う機能の有無で動作を分ける処理 | — |
| 3 か所の宣言が同じ値かを見るテストを `tests/core/test_version.py` に移し、`tests/sh/test_compat_skew.py` を消す | 比べる処理が無いので sh のテストは要らない。宣言の一致は残す | 一致の確認も消す |

## 得たもの・失ったもの

- 得たもの: sh の全ての入口から `--version` の起動と比べが消え、操作ごとの書き忘れや、`$( )` の中で出した目印が親に残らない問題も無くなる。文面と案内の維持も要らない
- 失ったもの: 食い違った版が混ざった（片方だけ配り直した・手で一部だけ置き換えた）ときに、直し方を言わずに予期しない動作をしうる。実行ファイルが sh の呼び出しの形を知らないと断る場合は、実行ファイル側の断りの文面だけが手がかりになる

## 同時配布の確かめ方

- `scripts/ccnavi-package.sh` は、組み立てた実行ファイルの `compat`、VS Code 拡張の `EXTENSION_COMPAT`、Chrome 拡張に埋め込んだ互換の版が実行ファイルの `COMPAT` と合うときだけ、その拡張を配布物に入れる。配布物の `VERSION` に実行ファイルの版・コミット・`compat` を書く。sh の `CCNAVI_COMPAT` との一致は `--lint` と `tests/core/test_version.py` が見る
- `scripts/ccnavi-install.sh`・`scripts/ccnavi-doctor.sh` は、sh と実行ファイルの互換の版を比べない。配布物は 1 つにまとまっているので、配布物から入れる限り同時に揃う。比べるかどうかは、配り方の運用で足りなくなったときに改めて決める

## 採らなかった案

- 食い違いの通知を残して呼び出しだけ減らす案は、前提が変わらないので採らない
- Chrome 拡張の互換の版の突き合わせ（`ccnavi_chrome.py`）は、別の配布経路で同時に配れないので触らない
