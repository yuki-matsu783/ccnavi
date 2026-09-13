---
version: 1
ticket: launcher-scripts-09
parent: launcher-scripts
phase: 4
predecessors:
- launcher-scripts-08
title: 写す手順を直す（2 回目の敵対的レビューの指摘）
rationale: 'launcher-scripts-08 で直した `wip/design/scripts/COPY.md` を sonnet でもう一度敵対的にレビューし、

  1 回目の直し 5 点が直っていることは実測で確かめられた。そのうえで、うまくいかなかったときの分岐（pull の失敗、revert の衝突、

  作業ツリーが片付いたあとの戻し方）と、確かめ方の基準の緩さが 6 件挙がったので直す。写す sh の本体は変えない。

  '
human_review:
  required: true
  reason: 人が保護された置き場へ写し、hook の起動先を切り替える手順の文面を直すため
allow:
- match: Write|Edit
  glob: wip/design/*
ccnavi_approved:
  approved_at: 2026-09-14T08:15:22+0900
  source_tree: launcher-scripts
  source_path: /Volumes/Data/git/ccnavi/.claude/worktrees/launcher-scripts/wip/tickets/todo/launcher-scripts-09.md
---

# 写す手順を直す（2 回目）

## 直すこと（すべて `wip/design/scripts/COPY.md`）

| # | 重大度 | 箇所 | 今の問題 | 直し方 |
|---|---|---|---|---|
| 1 | 中 | 9 の `git pull` | pull が通らないとき（ワークスペースルートに未コミットの変更がある、fast-forward にならない、衝突する）の分岐が無い。CLAUDE.md の運用ではワークスペースルートに他セッションの書きかけがありうる | pull の前に `git status --short` を見る段を足す。pull が通らなかったら、他人の変更に触らず（commit・stash・reset をしない）止まり、`build.py` も開き直しもしない。pull が失敗した状態では `settings.json` は前の綴りのままなので、そのまま開き直しても前の形で動く、と書く |
| 2 | 中 | 戻し方の A（2 つとも） | `cd .claude/worktrees/launcher-scripts` から始まるが、MR を Ready にしたあとは作業ツリーを片付ける運用なので、push 後に戻したくなったときには作業ツリーが無いことがある | 作業ツリーが無ければ、ブランチ `launcher-scripts` を作業ツリーとして作り直してから打つ、と足す（`ccnavi-git.sh worktree add .claude/worktrees/launcher-scripts launcher-scripts` の形。エージェントの規則に合わせてラッパーを通す綴りも書く） |
| 3 | 中 | 戻し方の `git revert` | 衝突したときの指示が無い（間に別の変更が同じ行に入っていた場合） | 衝突したら `git revert --abort` で打つ前の状態に戻し、手で解かずに相談する、と足す |
| 4 | 低 | 戻し方「A の途中」の先頭 2 行 | 2 の写す段より前にやめた場合、`git rm --cached` と `rm` が「無い」で失敗し、壊れたように見える | 無ければ飛ばしてよいことを書き、`rm -f` にする。`git rm --cached` は `git ls-files` で在るときだけ打つ形にする |
| 5 | 低 | 8 の lint | 「error を出さないことを確かめます」だけで、6 に比べて基準が緩い | 見るもの（終了コードと、error の行の出方）を `ccnavi/lint.py` の実際の出力に合わせて書く。実物を回して文面を確かめる |
| 6 | 低 | 5 と 9 の `git status --short .ccnavi/bin/` | 置き場が無いと `warning: could not open directory` を出すので、「何も出さない」が字義どおりでない | 置き場が無いときにこの警告が出ること、その場合は直前の `ls` が先に失敗しているので 5 に戻る、と書く |

## 確かめること

- 1・3・4・6 のコマンドを、スクラッチパッドの使い捨てのリポジトリで打ち、書いた出力になる（pull の拒否、revert の衝突と `--abort`、無いファイルへの `rm -f`、無い置き場への `git status`）
- 5 は、隔離した場所で `uv run python -m ccnavi --lint --log "" --state ""` を、新しい綴りで実行ビットあり・なしの 2 通りで回し、終了コードと出力を書き写す
- `wip/design/scripts/ccnavi-launcher.sh` に差分が無い

## やらないこと

- 写す sh の本体を変える
- 1 回目・2 回目で「問題なし」と確かめられた段（3 段の確かめ、`check-ignore`、`.gitignore` の置き換え、`commit -- <パス>` の注意、相互参照）を書き換える
