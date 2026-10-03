---
type: guide
title: 実行環境と呼び名
description: 対応する実行環境（Windows、macOS、Linux）と、プロジェクトで使う用語の定義
tags: [sh-scripts, environment]
keywords: [実行環境, 環境, Windows, macOS, Linux, Git Bash, WSL, ワークスペースルート, 統合先ブランチ, 用語, sh, bash, BSD]
---

# 実行環境と呼び名

## 実行環境

- 対象はWindowsのGit Bash、WindowsのWSL、Claude Code on the web（Linux）、macOSの4つ。どれでも動くように書く
- macOSの`sh`はbash 3.2で、`sed`などはBSD版。変数のすぐ後ろに全角文字を続けるときは`${var}`のように波括弧で囲む。
  `$( )`の中に`case`を書かない。どちらも`tests/core/test_sh_portability.py`が検査する
- 使える道具は`jq` 1.6、Node 22（pnpm 10）、Python 3.12（uv）、go。ほかの道具がある前提で書かない
- Claude Code on the webには`.ccnavi/bin`が無い。ccnaviのhookを動かすには、
  `uv run --with pyinstaller python build.py`でビルドする。ビルドしないとランチャーが127で終了し、hookは
  何もしない（2026-09-25にClaude Code 2.1.282で確かめた）

## 呼び名

- ワークスペースルート = Claude Codeを起動した場所（`CLAUDE_PROJECT_DIR`）
- gitプロジェクトルート = `.git`がある場所。ワークスペース自身、`projects/`の下の各プロジェクト、
  ワークツリーがそれぞれ持つ
- 統合先ブランチ = 作業を合流させるブランチ
