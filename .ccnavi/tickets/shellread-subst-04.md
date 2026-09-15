---
version: 1
ticket: shellread-subst-04
parent: shellread-subst
phase: 3
predecessors:
- shellread-subst-03
title: coproc の後ろをコマンドの先頭として読む
rationale: "3 番目（shellread-subst-03）を閉じたあとの敵対的レビュー（2026-09-15）で、予約語の直後を\nコマンドの先頭として読む変更（設計\
  \ §0 の 4 つ目の穴）に `coproc` が漏れていると分かった。\n`coproc { find . -delete; }` の find が `(^|\\\
  x00)` の直後に立たず、find-writes に当たらない\n（ask。deny のはず）。zsh で中身が実行されることを実測した。3 番目が埋めた穴と同じ種類なので、\n\
  利用者の判断（2026-09-15）でフェーズ 3 の中で直す。\n\nレビューで出た残りの 3 件は、同じ判断で次のとおり扱う。ここでは直さない。\n- `$(\
  \ )` の中の、コマンドの先頭ではない `case` の語でも縮退する（厳しい側の誤検知）。今のまま残し、\n  4 番目（文書）で許容した誤検知として書く\n\
  - 置換を大量に並べると読みだけで遅い（8 万個・949KB で 3.6 秒。読み終わったあと期限切れで止める側に倒れる）。\n  今のまま残し、4 番目（文書）で書く\n\
  - ブレース展開 `{git,push,origin,main}`（bash が実行し、読みでは 1 語）。今回の変更の前からある限界で、\n  `ccnavi-review.sh\
  \ handoff` で別の issue に切り出す\n"
human_review:
  required: true
  reason: 判定の読みが変わる（止まるものが増える向き）。3 番目と一緒にフェーズ 3 のレビューで見る
allow:
- match: Write|Edit
  glob: ccnavi/*
- match: Write|Edit
  glob: tests/*
ccnavi_approved:
  approved_at: 2026-09-15T16:40:38+0900
  source_tree: shellread-subst
  source_path: /Volumes/Data/git/ccnavi/.claude/worktrees/shellread-subst/wip/tickets/todo/shellread-subst-04.md
---

# 実装: coproc の後ろをコマンドの先頭として読む

## shell の実測（2026-09-15、macOS）

中身を「呼ばれたら記録を残す関数」に置き換えて走らせた。

| 形 | bash 3.2 | zsh |
|---|---|---|
| `coproc { M; }` | 構文エラー | 実行 |
| `coproc M` | コマンドが無い | 実行 |
| `coproc while true; do M; break; done` | 構文エラー | 実行 |
| `coproc ( M )` | 構文エラー | 実行 |
| `coproc if true; then M; fi` | 構文エラー | 実行 |
| `coproc NAME M` | コマンドが無い | NAME を実行 |
| `coproc NAME { M; }` | 構文エラー | 構文エラー |

bash 3.2 は `coproc` を持たない。bash 4 以降（Git Bash・WSL・Linux）は `coproc NAME <複合コマンド>` の
名前付きの形を持つが、この機械に無いので実測していない（知られている構文から扱いを決める）。

## やること

1. `ccnavi/shellread.py`
   - `_RESERVED` に `coproc` を足す。`coproc find . -delete` と `coproc { … }` の後ろがコマンドの先頭になる
   - 名前付きの形: `coproc` の直後に 1 語だけが立ち、その次が `{` か複合コマンドの予約語
     （`while` `until` `for` `if` `case` `select`）なら、その 1 語も 1 本のコマンドとして切る。
     `(` は演算子なので今も切れる。切り方を広く取っても、名前が 1 本のコマンドとして読まれるだけで、
     ルールに当たる語が増えることは無い（厳しい側）
2. `tests/test_shellread.py` と `tests/test_repo_rules.py`
   - `coproc { find . -delete; }`、`coproc find . -delete`、`coproc NAME { find . -delete; }` が
     find-writes の deny になること（今はどれも find が先頭に立たない）
   - `coproc while true; do find . -delete; done` と `coproc ( find . -delete )` が deny のままであること
     （今も `do` と `(` の後ろで切れている）
   - `coproc NAME find . -delete`（zsh は NAME を実行する）は、今と同じく find を先頭に読まなくてよい
     （find は NAME の引数）
   - `echo coproc`（コマンドの先頭ではない）の読みが変わらないこと
3. 全テスト、ruff、`.ccnavi/common/rule-samples.yml` の見本が変わらないことを確かめる

## 範囲

`ccnavi/` と `tests/` だけ。
