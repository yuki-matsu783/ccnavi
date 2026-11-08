---
type: guide
title: 組み立てと配布物
description: scripts/ccnavi-build.sh（Go）での実行ファイルの組み立てと別の機械向けの組み立て、.ccnavi/bin/ へのコピー、ワークツリーとワークスペースルートでの違い、dist/ を直に起動する方法、配布する sh の確かめ方
tags: [development, build]
keywords: [ccnavi-build.sh, go build, GOOS, GOARCH, クロスコンパイル, ldflags, dist, .ccnavi/bin, ccnavi-launcher.sh, CCNAVI_ENTRY_PATH, CCNAVI_SH_DIR, CCNAVI_GUARD_CORE_FILES, 配布物, test_e2e_sh, ワークツリー]
---

# 組み立てと配布物

実行ファイルの組み立て（`scripts/ccnavi-build.sh`）と、配布する sh の確かめ方。開発環境のコマンドは[開発の入口](../../docs/40_development.md)にある。

## 組み立て

```sh
sh scripts/ccnavi-build.sh                           # この機械向け
sh scripts/ccnavi-build.sh --target windows-x86_64   # 別の機械向け
```

実行ファイルは Go（`go/cmd/ccnavi`）を `go build` で組んだ 1 本で、Windows なら `dist/ccnavi/ccnavi.exe`、ほかは `dist/ccnavi/ccnavi`。
`--target` は `<os>-<arch>`（`linux`・`darwin`・`windows` と `x86_64`・`arm64` の組）で、振り分けの sh が探す置き場の名前と同じ語。省くとこの機械向け。
CGO を切って組むので、別の機械向けも C の道具なしで組める。どの向きを組んだかは `dist/ccnavi.target` に書く（`ccnavi-setup.sh` と `ccnavi-package.sh` が読む）。
組み立ての元のコミットは `-ldflags "-X ccnavi/internal/entry/version.commit=<sha>"` で埋め、`ccnavi --version` の `commit` に出る。未コミットの変更があれば `-dirty` を付け、git が無ければ `unknown`。
`dist/`・`build/`・`.ccnavi/bin/` は git 管理外。

このリポジトリの hook も、配布先と同じく振り分けの sh（`.ccnavi/scripts/ccnavi-launcher.sh`）を通る。
`ccnavi-build.sh` は `dist/ccnavi/` に組み立てたあと、それを `.ccnavi/bin/<os>-<arch>/` へコピーする（隣の `<os>-<arch>.new` にコピーし切ってから入れ替える）。別の機械向けに組んだときも、その向きの置き場へ入るだけで、この機械の置き場は変わらない。
コピーする段で失敗したら次の行を出して終了コード 1 で終わる。原因（ディスク、権限、Windows で走っている実行ファイルのロック）を直して回し直す。

```
dist/ は新しい。.ccnavi/bin/<target>/ は前のまま
```

## ワークツリーとワークスペースルート

- ワークツリーで組み立てると、コピー先はそのワークツリーの `.ccnavi/bin/` になり、走っている hook は変わらない
- ワークスペースルートで組み立て直すと、セッション開始で取った実行ファイルのバックアップと食い違う。`CCNAVI_GUARD_CORE_FILES` が `enable` なら次のツール呼び出しのあとでバックアップの版に戻される（`dry-run` なら報告だけ）。ワークスペースルートで組み立てたら、セッションを開き直す

仕組みは設計書の導入と配布の設計（`startup-config.md`）の「導入と配布」の節にある。

## 実行ファイルの選ばれ方

- `bin/` に実行ファイルが無いときは、振り分けの sh が PATH の `ccnavi` を起動する（`bin/` が先）。`dist/ccnavi/` を PATH に通すと、組み立てたあとのコピーなしで hook が動く
- PATH の実行ファイルも自己防衛が守る。テストは `tests/__init__.py` が PATH から ccnavi を外して回す
- `dist/` を直に起動したいときは、`.claude/settings.local.json`（追跡しない）の `env` で `CCNAVI_ENTRY_PATH` を `dist/ccnavi/ccnavi` にする。自己防衛も同じ env を読むので、守る実体と起動する実体は食い違わない。代償は、その機械では振り分けの sh の不具合に気づけないこと
- `ccnavi.settings.local.json`（追跡しない）のキー `bin` でも `CCNAVI_ENTRY_PATH` を上書きできる。変わるのは自己防衛が何を守るかだけで、hook が何を起動するかは変わらない

## 配布する sh を確かめる

```sh
uv run python -m unittest tests.e2e.test_e2e_sh -v
```

実行の最初に出る `sh =` がワークスペースルート側を指していることを確かめる。実際に使われるのはワークスペース側の 1 本だけ。
コピーする前の版を測るときは `CCNAVI_SH_DIR=<場所>` で差し替える。組み立て済みの実行ファイルを試すので、`go/` を直したら組み立て直してから回す（無ければ skip）。モード B（`projects/` を使う形）に触ったら回す。
