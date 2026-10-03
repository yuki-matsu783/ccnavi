---
type: guide
title: 実行環境と呼び名
description: 対応する実行環境（Windows、macOS、Linux）と、プロジェクトで使う用語の定義、文書やコメントでのADRへの触れ方
tags: [sh-scripts, environment]
keywords: [実行環境, 環境, Windows, macOS, Linux, Git Bash, WSL, ワークスペースルート, 統合先ブランチ, 用語, sh, bash, BSD, ADR, ADR番号, コメント]
---

# 実行環境と呼び名

## 実行環境

- 対応する実行環境は、WindowsのGit Bash、WindowsのWSL、Claude Code on the web（Linux）、macOSの4つ。shはどの環境でも動くように書く
- macOSの`sh`はbash 3.2で、`sed`などのコマンドはBSD版。変数のすぐ後ろに全角文字を続けるときは`${var}`と括る。
  `$( )`の中には`case`を書かない。この2点は`tests/core/test_sh_portability.py`が検査する
- 使える道具は`jq` 1.6、Node 22（pnpm 10）、Python 3.12（uv）、goだけ。これ以外の道具がある前提で書かない
- Claude Code on the webには`.ccnavi/bin`が無い。ccnaviのhookを動かすには、
  `uv run --with pyinstaller python build.py`でビルドする。ビルドしていないとランチャーが終了コード127で終わり、
  hookは何もしない

## 呼び名

- ワークスペースルート: Claude Codeを起動したディレクトリ（`CLAUDE_PROJECT_DIR`）
- gitプロジェクトルート: `.git`があるディレクトリ。ワークスペース自身、`projects/`の下の各プロジェクト、
  各ワークツリーがそれぞれ持つ
- 統合先ブランチ: 作業を合流させるブランチ

## 文書とコメントでのADRの扱い

- 文書・コメント・利用者に見える文言（print、拒否文、`rules.yml`の`message`、lintの文言）では、ADR番号で言及しない。
  決めた中身と理由をその場で書く。読み手がADRを開かなくても、なぜそうなっているかが分かるようにする
- ADRの中の節番号や決定の記号（`D11`、`段階 2c`など）も同じ扱いにする
- 番号を使うのは、ADR同士の置き換え関係を書くとき（`docs/adr/`の中で「ADR-NNNNの置き場を置き換える」と書くなど）だけ
- 番号だけでは、読み手はADRを開くまで意味が分からない。ADRが後で改められても、番号を引いた側との食い違いに気づけない
