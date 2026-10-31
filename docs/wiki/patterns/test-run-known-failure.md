---
title: 既知の失敗がある間の全件テスト
type: learning-wiki
target: skill
sources: [feature-207]
description: 既知の失敗で python -m tests が途中で止まり、bench が数分かかり、レビュー準備中は全件を回せないパターン
tags: [skills, feedback]
keywords: [テスト, 全件, 既知の失敗, python -m tests, discover, bench, レビュー準備中, finish]
---

# 既知の失敗がある間は全件の結果が見えない

## 説明

既知の失敗が 1 本あると、`python -m tests` は最初の失敗で新しいプロセスを起こすのをやめ、残りの結果が見えない。
`tests/core/bench_deadline.py` は数分かかる。親の最後のフェーズの子を finish した後のレビュー準備中は、シェルの実行が止まって全件を回せない。

## 原因

- `python -m tests` は落ちた 1 本の出力だけを出す作り
- 実ルールを使う bench は `*x*` 形の glob の照合が入力の長さの 2 乗で伸びる
- レビュー準備中はシェルの実行が止まる

## 操作の例

feature-207 で、既知の失敗 1 本のために全件の結果が見えず、bench を前で回して数分待ち、最後の子を finish した後に全件を回せなかった。

## 回避策

- 既知の失敗がある間は、グループごとに `discover -s tests/<グループ> -t .` で回し、失敗の数を既知と比べる
- bench は名指しで、ターンの中ならバックグラウンドで回す
- 全件は、親の最後のフェーズの子を finish する前に回す
