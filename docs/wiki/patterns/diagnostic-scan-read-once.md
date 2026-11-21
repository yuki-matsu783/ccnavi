---
title: 診断に走査を足すときの読みの回数と lint のテスト
type: learning-wiki
target: skill
sources: [feature-207]
description: --explain と --lint に走査を足すと、置き場の読み直しで rawshare のテストが、新しい警告で lintops のテストが落ちるパターン
tags: [skills, feedback]
keywords: [explain, lint, 診断, 走査, 置き場, copies, rawshare, lintops, 合流]
---

# 診断に走査を足すと、読みの回数と既存の lint のテストにヒットする

## 説明

`--explain` と `--lint` に走査を足すとき、承認済みチケットの置き場を読み直す作りにすると `go/internal/clitest/ticket/life/rawshare_test.go` が落ちる。
診断に足した新しい警告は、名前で警告の行を拾う `go/internal/clitest/ticket/core/lintops_test.go` の既存テストに取り違えられ、並行した子を合流した後にだけ落ちた。

## 原因

- 置き場は 1 回の起動で 1 度だけ読む約束で、rawshare のテストが読んだ回数を数えて固定している
- lintops のテストは行を名前で拾うので、別の子が足した警告と区別できない。子ごとのブランチでは通る

## 操作の例

feature-207（#210）で `--explain` と `--lint` に元のプロジェクトが無いワークツリーの走査を足した。サブエージェントの報告で、約束を知らないと読み直す作りになりかけたこと、合流後に lintops のテストが落ちたことが分かった。

## 回避策

- 1 度読んだコピー（copies）を走査に引数で渡す
- 診断に警告を足したら、並行した子を合流した後に `go test ./internal/clitest/ticket/...` を回す
