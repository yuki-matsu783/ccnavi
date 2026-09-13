---
version: 1
ticket: shellread-sep-04
parent: shellread-sep
phase: 4
predecessors: [shellread-sep-03]
title: shellread の印が 2 つになったことを文書に書く
rationale: |
  3 番目（実装）で、shellread の印を「コマンドの区切り（\x00）」と「語の中の切れ目
  （\x01）」に分けた。README と ccnavi.md は、どちらも印が 1 つである前提で書かれて
  いるので直す。

  3 番目のレビューでは、main から入った 2 か所（phase.py の `_NOT_PREVIEW` と
  selfguard.py の `_TERM` / `_END`）を、語の中の印もまたがない・語の終わりに数える
  形にして今の判定を保つことが受け入れられた。ccnavi.md にはこの 2 か所の説明が
  あるので、同じ理由をそこにも足す。

  設計書（wip/design/shellread-sep.md）§2 に上の 2 か所を足すのは、このフェーズの
  範囲の外。全体計画のレビューが済んだあとの、フィードバック計画で扱う。
human_review:
  required: true
  reason: 外から見える約束（README の Bash の読み方）を書き換える
allow:
  - match: Write|Edit
    glob: "README.md"
  - match: Write|Edit
    glob: "ccnavi.md"
---

# 文書: shellread の印を 2 つに分けた

## やること

1. `README.md` 717 行目からの節「Bash のコマンドは実行される部分だけを見る」
   - 737 行目「引用が 1 語につないだ空白は…」の段落に、次の 2 点を足す
     - `grep -n "git push" README.md` のような引用付きの grep / cat / find が、
       `prefer-read-grep` / `prefer-glob`（allow）に当たるようになった
     - 印はコマンドの間と語の中で別の文字である。ルールの `[^\x00]*` は
       「同じコマンドの中」を指す
   - 741 行目「語の中に入った `>` …」は、印の文字が語の中の印であることが分かる形に直す
2. `ccnavi.md`
   - 373 行目: 入力から取り除くのは `\x00` と `\x01` の 2 文字にする
   - 383-386 行目: コマンドの間は `\x00`、語の中の空白と語の中の演算子の両側は `\x01` と書き分ける
   - 731 行目: `_NOT_PREVIEW` が語の中の印もまたがないことと、その理由を足す。
     またぐと、引数の値に書いた `--preview` が承認の免除になる
   - 1358 行目: 綴りの例を `_NOT_A_WORD` を含む形に直す。語の中の印も語の終わりに
     数える理由を足す。数えないと、印を分けただけで `rm ".ccnavi x"` が通る
3. 変えないもの
   - `HANDOVER.md` 209 行目（プロジェクト名の式。印を分けても判定は変わらない）
   - 読めない形として許容している誤検知（`grep -n "<<" f` など）の記述

## 完了の条件

- 上のすべての場所が、実装（`ccnavi/shellread.py`、`ccnavi/selfguard.py`、`ccnavi/phase.py`）と食い違わないこと
- 文書に書いた見本の判定が `uv run python -m ccnavi --test Bash '<見本>'` の結果と一致すること

## 範囲

`README.md` と `ccnavi.md` だけ。`wip/design/shellread-sep.md` と `.claude/ccnavi/rule-samples.yml` はこのチケットの外。
