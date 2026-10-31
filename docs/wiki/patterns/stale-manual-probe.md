---
title: 古くなった手動の probe
type: learning-wiki
target: skill
sources: [feature-207]
description: tests/manual/ の probe が仕様変更に追いつかず古くなり、止まった理由を見ずに偽陽性の PASS を出すパターン
tags: [skills, feedback]
keywords: [probe, manual, tests/manual, 手動, 偽陽性, PASS, 前提]
---

# 手動の probe は静かに古くなる

## 説明

`tests/manual/` の probe が、仕様の変更（親を先に start、`review-request` の `--eli5`、`decide` の入力など）に追いつかず古くなっていた。
「止まる」ことを確かめる段で、止まったことだけを見て PASS とし、止まった理由が別だった。

## 原因

- `tests/manual/` は自動テストが呼ばないので、仕様が変わっても落ちない
- 止まった理由（拒否の文面やコード）まで見ないと、前提の崩れで止まったものも PASS に見える

## 操作の例

feature-207 で `tests/manual/` の probe を回したとき、手順の前提が仕様の変更に追いついておらず、「止まった」ことだけを見て PASS としたが、止まった理由は期待と別だった。

## 回避策

- 回す前に、probe の手順が今の前提に合っているかを読む
- 止まった理由まで期待と比べる
- 前提を直したら、probe の docstring の「用意するもの」も同じ変更で直す
