---
title: つまずきやすい点
type: guide
description: 導入や設定でつまずきやすい点と直し方。hook が動かない、env が反映されない、ワークツリーが消せない、実行ファイルが見つからないなど
tags: [user-guide, troubleshooting, settings]
keywords: [つまずき, トラブル, hook が動かない, env, 再読み込み, CLAUDE_PROJECT_DIR, CCNAVI_BIN_PATH, 127, 126, 実行ビット, Windows, ワークツリーが消せない, matcher, projects, 未知のキー]
---

# つまずきやすい点

導入や設定で起きやすいつまずきを、症状から引けるように並べる。実測の根拠は[実測で確かめた前提](../design/appendix.md)（Claude Code の挙動）にある。

## 設定が反映されない

- `env` の値を変えたのに古いまま。`env` ブロックは再読み込みされない。セッションを開き直すまで古い値が残る。`command` は即座に反映される
- `env` に `${CLAUDE_PROJECT_DIR}` を書いたら効かない。`${CLAUDE_PROJECT_DIR}` は hook の `command` では展開されるが `env` では展開されない。`env` には相対パスを書く
- `.claude/settings.json` に書いたら読まれない、または弾かれる。`.claude/settings.json` は未知のトップレベルキーを拒否する。ccnavi の設定は `env` に環境変数で書く
- `disable` と書いたのに止まる。`disable` は `.claude/settings.json` に書いても有効にならない。一時的に外すなら、セッションを起動する側の環境から渡す（[hook の挙動](hooks.md)の「動作モード」）
- Bash ツールの中で `CLAUDE_PROJECT_DIR` を使えない。ツールのプロセスには `CLAUDE_PROJECT_DIR` が入っていない。hook の環境にだけ来る

## hook が動かない

- hook が起動しない。`env` から `CCNAVI_BIN_PATH` の 1 行が消えると、hook が起動しなくなる。`sh scripts/ccnavi-setup.sh <ワークスペースルート> --check` で揃っていないところを見る（[導入と配布](install.md)）
- 一部のツールで hook が起動しない。`matcher` を絞ると、書かなかったツールで hook 自体が起動しなくなる。`matcher` は絞らない（[設定](settings.md)）
- `projects/` のプロジェクトの中で hook が動かない。プロジェクトの中で Claude Code を起動すると `CLAUDE_PROJECT_DIR` がプロジェクトになり、ワークスペースの `.claude/settings.json` が読まれない。ワークスペースルートで起動して `cd` で入る
- `ccnavi` を端末で直接打ったら、意図しない場所が根と見なされた。`CLAUDE_PROJECT_DIR` が無い端末や Bash ツールから `.claude/` を持つプロジェクトの中で打つと、`--root` を渡さない限りそのプロジェクトが根になる。`--root` を付けるか、sh のラッパーを通す

## 実行ファイルが見つからない・動かない

- 終了コード 127。この機械の `<os>-<arch>` に合う実行ファイルが `.ccnavi/bin/` にも PATH にも無い。メッセージに探した置き場が出る。その機械で組み立てて配る（[導入と配布](install.md)）。PATH に通すなら、実行ファイル単体ではなく、隣に `_internal/` がある `dist/ccnavi/` を通す（単体のコピーは動かない）。PATH の ccnavi は sh と版を揃えて配られないので、古い版が動いていないか `ccnavi --version` で確かめる
- 終了コード 126。実行ファイルか、振り分けの sh の実行ビットが外れている。別の機械で clone した直後に起きることがある。`sh scripts/ccnavi-setup.sh <ワークスペースルート>` を打ち直すと付け直す。`ccnavi --lint` も error として報告する
- 別の機械で組み立てたものが動かない。実行ファイルは組み立てた機械の OS と CPU でしか動かない。Windows と WSL で同じフォルダを開くなら、それぞれの機械で配る

## 記録

- 記録の数が足りない。ローテートした分は `logs/decisions.<日時>.jsonl` にある。`logs/decisions*.jsonl` を渡して合わせて数える（[導入と配布](install.md)の「記録を読む」）
- 記録をコミットしてしまいそう。記録には絶対パスとコマンド全文が入る。`logs/` はコミットしない

## ワークツリー

- Windows でワークツリーが消せない。誰かのテストが同じ実体のファイルを読み込んでいる間は消せない。テストが走っていないときにやり直す。失敗したら、残ったディレクトリを `mv` で `.claude/worktrees/` の外へ出して `git worktree prune`。先に `sh .ccnavi/scripts/ccnavi-clean.sh <名前>` で生成物を消すと、`node_modules` で止まる分は避けられる。理由と詳細は[実行環境の決まり](../claude/environment.md)にある
- VS Code からワークツリーが見えない。`.vscode/settings.json` に `git.detectWorktrees` と `git.repositoryScanMaxDepth` が無い。導入スクリプトが書くが、`false` と書いてあれば変えない。`.vscode/settings.json` にコメントや末尾のカンマがあると何を足せばよいかだけを出す（[導入と配布](install.md)の「VS Code からワークツリーを見る」）

## ルール

- ワークツリーで直したルールが効かない。統合されるまで反映されない（[ルールの書き方](rules.md)）
- 同じ `id` で中身が違うルールが両方当たる。`--lint` が warn で言う（[ルールの書き方](rules.md)）
- YAML が読めない。引用符の注意は[ルールの書き方](rules.md)にある
