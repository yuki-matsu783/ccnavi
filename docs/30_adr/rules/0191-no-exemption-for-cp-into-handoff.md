---
type: adr
title: 守る場所のファイルを wip/handoff/ へ cp で写す形も止め、ccnavi-stage.sh を案内する
description: シェルから書き込む形の保護から、ワークスペースルートの wip/handoff/ へ写す cp と install を細かく読んで外していた例外をやめる。止めた文が ccnavi-stage.sh で写す手順を案内する
tags: [rules, selfguard, handoff]
keywords: [自己防衛, guard-setting-files, cp, install, wip/handoff, ccnavi-stage.sh, 例外, handoff_copy_exempt]
---
# ADR-0191: 守る場所のファイルを wip/handoff/ へ cp で写す形も止め、ccnavi-stage.sh を案内する

状態: 採用

## 状況

`builtin:guard-setting-files` は、cp の行き先の最後の語に守る名前があれば止める。守る場所を直すために
`cp .ccnavi/scripts/ccnavi-git.sh wip/handoff/ccnavi-git.sh` と写す形まで止まるので、ワークスペースルートの
`wip/handoff/` へ写す `cp` `install` だけを、行き先を実際のパスに解いて外していた。置き場を足場にして守る場所へ
書かせないため、選択肢・読めない文字・`..`・リンク・ハードリンク・つないだコマンドを 1 つずつ見ていた。

写す手順そのものは `ccnavi-stage.sh <元のパス>` が持っている。sh の中でコピーするので hook の判定は写す cp を見ず、
実行権限・バイナリ・大きいファイル・`.claude` の階層の `handoff-.claude` への読み替えも扱う。手で cp すると
この読み替えが抜ける。ワークツリーの `wip/handoff/` は、もともと例外の外だった。

## 決定

例外をやめ、`wip/handoff/` へ写す `cp` `install` も、ほかの書き込みと同じく止める。止めた文の `next` が、
`ccnavi-stage.sh <元のパス>` で写し、写しを直してユーザに `ccnavi-user-handoff.sh` での取り込みを頼む手順を案内する。
置き場の中を名前で書くツールで直す例外（`handoff_write_exempt`）は残す。

## 理由

写す道は `ccnavi-stage.sh` の 1 本で足り、手の cp は読み替えを抜かす。例外を正しく保つには、cp と install の選択肢の
省略やリンクの形を追い続ける必要があり、外し方を誤ると置き場が守る場所へ書く足場になる。

## 得たもの・失ったもの

- 得たもの: シェルから書き込む形の保護に、式の外で外す経路が無くなる。写す手順がワークスペースルートとワークツリーで同じになる。
- 失ったもの: 手で cp して写す形は止まり、1 回は `ccnavi-stage.sh` で写し直す手間が増える。

## 採らなかった案

| 案 | 落とした理由 |
|---|---|
| 例外を残し、ワークツリーの置き場にも広げる | 選択肢とリンクの形を読む経路が広がり、`.claude` の読み替えは抜けたまま |
