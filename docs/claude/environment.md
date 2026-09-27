# 実行環境と呼び名

## 実行環境

- Windows の Git Bash、Windows の WSL、Claude Code on the web (Linux)、macOS の 4 つ。どれでも動くように書く
- macOS の `sh` は bash 3.2、`sed` などは BSD 版。変数のすぐ後ろに全角文字を続けるときは `${var}` と括る。
  `$( )` の中に `case` を書かない。どちらも `tests/core/test_sh_portability.py` が見る
- 使える道具は `jq` 1.6、Node 22 (pnpm 10)、Python 3.12 (uv)、go。これ以外がある前提で書かない
- Claude Code on the web には `.ccnavi/bin` が無い。ccnavi の hook を効かせるには
  `uv run --with pyinstaller python build.py` で組み立てる。無いとランチャーが 127 で終わり、hook は
  何もしない（2026-09-25、Claude Code 2.1.282 で実測）

## 呼び名

- ワークスペースルート = Claude Code を起動した場所（`CLAUDE_PROJECT_DIR`）
- git プロジェクトルート = `.git` がある場所。ワークスペース自身、`projects/` の下の各プロジェクト、
  ワークツリーのそれぞれが持つ
- 統合先ブランチ = 作業を合流させるブランチ
