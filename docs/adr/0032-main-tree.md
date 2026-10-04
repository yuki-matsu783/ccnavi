---
type: adr
title: ワークスペースルート直下の編集をルールで止める
description: main 作業木の直下ファイル編集をルールで保護する仕組み
tags: [rules, git, worktree]
keywords: [ワークスペース, main, ルール, 保護, 編集, 直下]
---

# ADR-0032: ワークスペースルート直下の編集をルールで止める

状態: 採用

## 状況

「編集は worktree で」は CLAUDE.md の指示だけで、hook はワークスペースルート直下の Write / Edit を
通していた（allow の `source` と `project-files`）。2026-09-11。

## 決定

ルールに `{root}`（ワークスペースルートの実パスに読み込み時に置き換わる表記）を足し、
deny の `main-tree` で「ワークスペースルートの下で、かつ `.claude/worktrees/` の外」を止める。
ワークツリーの中でも `.claude/settings*.json` は `guard-settings` で止める。ワークツリーの中は
`worktrees` の allow がまとめて通す。`source` と `project-files` は先に deny に当たって使われなくなるので消した。

## 理由

ルールの契約が先読みを禁じている（ADR-0010）ので、ワークスペースルートの直下の 1 段目で
場合分けする形（`.` で始まらない名前 / `.c` 以外で始まる隠し名 / `.cl` 以外 / `.claude/` の下の
`w` で始まらない名前）になった。`{root}` は「ワークスペースの下」を絶対パスの直書きなしに
書くためのもので、Windows と WSL と Linux で表記が分かれない。

hook・スクリプト・承認済みチケットは既存の deny が場所を問わず当たる。

## 採らなかった案

- CLAUDE.md の指示だけで運用する。プロンプトに書いた禁止は確率を下げるだけで 0 にはならない
- ワークスペースルート直下を丸ごと `deny` にする。`.claude/worktrees/` もその下なので、
  ルールの契約が先読みを禁じている以上、1 段目で場合分けするしかない

## 得たもの・失ったもの

- 得たもの: CLAUDE.md の運用が hook で担保される
- 失ったもの: このセッション自身がワークスペースルートで編集していたのは hook が dry-run
  だったから。enable に戻せば止まる
- `tests/test_root_placeholder.py` と `.claude/ccnavi/rule-samples.yml` に固定してある
