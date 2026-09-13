---
version: 1
ticket: launcher-scripts-08
parent: launcher-scripts
phase: 4
predecessors:
- launcher-scripts-06
title: 写す手順を直す（フェーズ 4 の敵対的レビューと観点別レビューの指摘）
rationale: 'launcher-scripts-06 の `wip/design/scripts/COPY.md` を、sonnet の敵対的レビューと
  haiku の観点別レビュー 4 本で

  確かめ、実測で裏の取れた指摘 5 件を直す。写す sh の本体（`wip/design/scripts/ccnavi-launcher.sh`）は変えない。

  '
human_review:
  required: true
  reason: 人が保護された置き場へ写し、hook の起動先を切り替える手順の文面を直すため
allow:
- match: Write|Edit
  glob: wip/design/*
ccnavi_approved:
  approved_at: 2026-09-14T01:06:54+0900
  source_tree: launcher-scripts
  source_path: /Volumes/Data/git/ccnavi/.claude/worktrees/launcher-scripts/wip/tickets/todo/launcher-scripts-08.md
started_at: 2026-09-14T01:08:12+0900
base_sha: 43efa03fd7d99f9ff9d910e96a8bacbaa85c208c
completed_at: 2026-09-14T01:12:40+0900
---

# 写す手順を直す

## 直すこと（すべて `wip/design/scripts/COPY.md`）

| # | 箇所 | 今の文面の問題 | 直し方 | 出どころ |
|---|---|---|---|---|
| 1 | 8「`git commit -- <パス>` を使わない」の理由 | 「`core.filemode=false` の機械でモードが戻る」は逆。実測では `filemode=false` では戻らず、`filemode=true`（macOS・Linux・WSL）でディスクの実行ビットが立っていないときに 100644 に落ちる | 理由を実測どおりに書き直す。「使わない」という指示は残す | sonnet（使い捨てのリポジトリで実測） |
| 2 | 5「`git status --short .ccnavi/` に出てよいのは `A  .ccnavi/scripts/ccnavi-launcher.sh` だけ」 | 親の作業ツリーでは、フェーズのマーカー（`.ccnavi/tickets/phases/…/4.pending` など）が未追跡で出ることがあり、断定が成り立たない | `.gitignore` が効いているかだけを見る形に絞る（`git status --short .ccnavi/bin/` が何も出さない、`git check-ignore -v .ccnavi/bin/<target>/ccnavi` が `/.ccnavi/bin/` の行を出す） | haiku 観点 2（作業ツリーの今の状態で確認） |
| 3 | 3 `.gitignore` の置き換え範囲 | 「4〜7 行を 8 行に置き換える」の「直したあと」が末尾に空行を含むので、指示どおりだと空行が 2 行になる | 置き換え範囲と「直したあと」の末尾の空行を揃える | sonnet（置換を再現して確認） |
| 4 | 戻し方 | 「A の途中（コミット前）」しか無い。8 で 1 回目・2 回目のコミットが済んだあと（push 前・push 後）の戻し方が無い。`enable` のとき、戻す手の変更を監視が控えから戻しうることも書いていない | コミット済み・push 前（コミットを打ち消すコミット）と push 後の戻し方を足す。`CCNAVI_GUARD_CORE_FILES=enable` なら、戻す間このワークスペースのセッションを止めることを「写す前に」と同じく書く | sonnet・haiku 観点 3 |
| 5 | 9（B）の前に閉じるもの | Claude Code のセッションだけを挙げている。VS Code のボード拡張が実行ファイルを起動していると、Windows では `_swap` の rename が落ちうる。初回は `.ccnavi/bin/<target>/` が無いまま env が sh を指し、hook が 127 になる | 閉じるものにボード拡張（VS Code のウィンドウ）を足す。組み立てが 1 で終わったら開き直さず、原因を直して回し直すことを書く | haiku 観点 3 |

## 採らなかった指摘（記録）

| 指摘 | 採らない理由 |
|---|---|
| 実装に `LAUNCHER_NAME`・`binary_clause` の 2 つの形・`locate.ts` の分岐が無い（haiku 観点 4、高 3 件） | 作業ツリーではなくワークスペースルート（main、フェーズ 3 の変更が入る前）のファイルを読んでいた。作業ツリーには入っていてテストも緑 |
| `settings.json` の 10 行目が直っていない（haiku 観点 2、高） | 人が直す前の状態を見ている |
| 7 の件数 244 がテストのメソッド数 156 と違う（haiku 観点 2、低） | grep で親クラスから継ぐテストを数えていない。244 は実際に回した件数（sonnet も 244 を実測） |
| `uname -sm` の出力に CR が混ざる、`cmp` が改行コードで食い違う（haiku 観点 1） | 推測。Git Bash の `uname` は LF で出し、同じ作業ツリーの中で `cp` した 2 本は同じ改行コードになる |
| A で組み立てを飛ばしてコミットすると判定が走らない（haiku 観点 3） | hook が読むのはワークスペースルート（main）の設定で、ブランチの `settings.json` は main に入るまで効かない。main に入ったあとは B が組み立ててから開き直す |
| squash マージでモード 100755 が落ちる（haiku 観点 3） | 推測。B の確かめる 2) で気づける |
| sh の冒頭のコメントがまだ無い ADR-0043 を指す（haiku 観点 4） | フェーズ 5（launcher-scripts-07）で作り、MR を Ready にする前に揃う。写す版を変えると、フェーズ 3・4 で確かめた版と食い違う |

## このチケットの外（別に扱う）

- **`.gitattributes` が無い。** Windows で `core.autocrlf=true` だと `.sh` が CRLF で取り出され、シバンの行が壊れて hook が起動しない（ゲートの sh も `sh` 経由で同じ影響を受ける）。今の配布先の `.ccnavi/bin/ccnavi` にも前からある種類の問題で、このチケットで変える場所ではない。`*.sh text eol=lf` を足すかを、別の issue で決める

## 確かめること

- 直した手順のコマンドを、隔離した場所（スクラッチパッドの使い捨てのリポジトリ）で打ち、書いた出力になる（1・2・3・4）
- `wip/design/scripts/ccnavi-launcher.sh` に差分が無い

## やらないこと

- 写す sh の本体を変える
- `.gitattributes`（上のとおり別に扱う）
