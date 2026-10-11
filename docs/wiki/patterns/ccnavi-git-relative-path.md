---
title: ワークツリーから相対パスで ccnavi-git.sh を呼ぶ
type: skill-wiki
description: ワークツリーの中で ../../../.ccnavi/scripts/ccnavi-git.sh と相対パスで呼ぶと、cwd によって届かないパターン
tags: [skills, feedback]
keywords: [ccnavi-git.sh, 相対パス, 絶対パス, ワークツリー, cwd, git, 入口, DENY_COMMAND_NAME_EXPANSION]
---

# ワークツリーから相対パスで ccnavi-git.sh を呼ぶと届かない

## 説明

ワークツリーの中で `sh ../../../.ccnavi/scripts/ccnavi-git.sh` と打つと、cwd によってはスクリプトに届かない。

## 原因

相対パスは cwd がワークツリーの最上段のときしか合わない。サブディレクトリに cd した後や、cwd が別のツリーに戻ったときは外れる。
commit スキルの「git の入口」と `docs/claude/worktree.md` の「基本」の表がこの書き方で、git を直接呼んだときの拒否の案内（絶対パスの形）と食い違っている。
`docs/claude/worktree.md` の直しは `chore-212-worktree-doc-absolute-path` で出した（スキルではないので `skill-impact.md` には載せない）。

## 操作の例

feature-207 で、サブエージェント 4 本以上がこの書き方で失敗したか迷った。コマンド名を変数に入れて試すと `DENY_COMMAND_NAME_EXPANSION` で止まった。

## 回避策

- `sh <ワークスペースルートの絶対パス>/.ccnavi/scripts/ccnavi-git.sh` と直書きする
- コマンド名やパスを変数に入れない
