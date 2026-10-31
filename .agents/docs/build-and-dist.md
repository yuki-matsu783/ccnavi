---
type: guide
title: 組み立てと配布物
description: build.py での実行ファイルの組み立て、.ccnavi/bin/ へのコピー、ワークツリーとワークスペースルートでの違い、dist/ を直に起動する方法、配布する sh の確かめ方
tags: [development, build]
keywords: [build.py, PyInstaller, onedir, dist, .ccnavi/bin, ccnavi-launcher.sh, CCNAVI_ENTRY_PATH, CCNAVI_SH_DIR, CCNAVI_GUARD_CORE_FILES, 配布物, test_e2e_sh, ワークツリー]
---

# 組み立てと配布物

実行ファイルの組み立て（`build.py`）と、配布する sh の確かめ方。開発環境のコマンドは[開発の入口](../../docs/40_development.md)にある。

## 組み立て

```sh
uv run --with pyinstaller python build.py
```

配布物は PyInstaller の onedir で組み立てる。Windows なら `dist/ccnavi/ccnavi.exe`、Linux なら `dist/ccnavi/ccnavi`。onefile は起動のたびにランタイムを展開して遅いので使わない。
`dist/`・`build/`・`.ccnavi/bin/` は git 管理外。

このリポジトリの hook も、配布先と同じく振り分けの sh（`.ccnavi/scripts/ccnavi-launcher.sh`）を通る。
`build.py` は `dist/ccnavi/` に組み立てたあと、それを `.ccnavi/bin/<os>-<arch>/` へコピーする（隣の `<os>-<arch>.new` にコピーし切ってから入れ替える）。
コピーする段で失敗したら次の行を出して終了コード 1 で終わる。原因（ディスク、権限、Windows で走っている実行ファイルのロック）を直して回し直す。

```
dist/ は新しい。.ccnavi/bin/<target>/ は前のまま
```

## ワークツリーとワークスペースルート

- ワークツリーで組み立てると、コピー先はそのワークツリーの `.ccnavi/bin/` になり、走っている hook は変わらない
- ワークスペースルートで組み立て直すと、セッション開始で取った実行ファイルのバックアップと食い違う。`CCNAVI_GUARD_CORE_FILES` が `enable` なら次のツール呼び出しのあとでバックアップの版に戻される（`dry-run` なら報告だけ）。ワークスペースルートで組み立てたら、セッションを開き直す

仕組みは設計書の導入と配布の設計（`startup-config.md`）の「導入と配布」の節にある。

## 実行ファイルの選ばれ方

- `bin/` に実行ファイルが無いときは、振り分けの sh が PATH の `ccnavi` を起動する（`bin/` が先）。`dist/ccnavi/` を PATH に通すと、組み立てたあとのコピーなしで hook が動く。実行ファイル単体のコピーは動かない（隣に `_internal/` が要る）
- PATH の実行ファイルも自己防衛が守る。テストは `tests/__init__.py` が PATH から ccnavi を外して回す
- `dist/` を直に起動したいときは、`.claude/settings.local.json`（追跡しない）の `env` で `CCNAVI_ENTRY_PATH` を `dist/ccnavi/ccnavi` にする。自己防衛も同じ env を読むので、守る実体と起動する実体は食い違わない。代償は、その機械では振り分けの sh の不具合に気づけないこと
- `ccnavi.settings.local.json`（追跡しない）のキー `bin` でも `CCNAVI_ENTRY_PATH` を上書きできる。変わるのは自己防衛が何を守るかだけで、hook が何を起動するかは変わらない

## 配布する sh を確かめる

```sh
uv run python -m unittest tests.e2e.test_e2e_sh -v
```

実行の最初に出る `sh =` がワークスペースルート側を指していることを確かめる。実際に使われるのはワークスペース側の 1 本だけ。
コピーする前の版を測るときは `CCNAVI_SH_DIR=<場所>` で差し替える。組み立て済みの実行ファイルを試すので、`src/ccnavi/` を直したら組み立て直してから回す（無ければ skip）。モード B（`projects/` を使う形）に触ったら回す。
