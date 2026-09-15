---
version: 1
ticket: shellread-subst-05
parent: shellread-subst
phase: 4
predecessors:
- shellread-subst-03
- shellread-subst-04
title: コマンド置換・改行・プロセス置換を読むようになったことを文書に書く
rationale: 'フェーズ 3（shellread-subst-03 / -04）で、shellread は shlex の前に原文を走査し、引用の中の `$(
  )` と

  バッククォート、プロセス置換、引用しない heredoc の本文の置換を独立したコマンドとして読むようになった。

  改行はコマンドの区切り、語の途中の `#` は文字、予約語（`coproc` を含む）の後ろはコマンドの先頭になった。

  文書はまだ前の読み（「`$( )` の中身は独立したコマンドになる」は引用の外だけで成り立つ、heredoc の本文は

  トークンの行番号で落とす）のまま。設計 wip/design/shellread-subst.md §2「文書」の表のとおりに直す。


  あわせて、フェーズ 3 の敵対的レビューで出た指摘のうち、利用者の判断（2026-09-15）で直さずに残した 2 つを、

  許容した限界として書く。

  - `$( )` の中の、コマンドの先頭ではない `case` の語でも `ambiguous-substitution` に縮退する（厳しい側の誤検知）

  - 置換を大量に並べると読みだけで遅い（8 万個・949KB で 3.6 秒。読み終わったあと期限切れで止める側に倒れる）

  ブレース展開の件は `ccnavi-review.sh handoff` で別の issue に切り出したので、ここでは限界の一覧に 1 行だけ書く。

  '
human_review:
  required: true
  reason: 文書フェーズは review mr。判定が変わる形（今 allow の heredoc の commit など）と回避策を利用者向けに書くので、書き方を見てもらう
allow:
- match: Write|Edit
  glob: README.md
- match: Write|Edit
  glob: ccnavi.md
- match: Write|Edit
  glob: HANDOVER.md
ccnavi_approved:
  approved_at: 2026-09-15T16:59:57+0900
  source_tree: shellread-subst
  source_path: /Volumes/Data/git/ccnavi/.claude/worktrees/shellread-subst/wip/tickets/todo/shellread-subst-05.md
---

# 文書: コマンド置換・改行・プロセス置換を読む

設計は `wip/design/shellread-subst.md`。§2「文書」の表と §3「判定が変わる見本」を元にする。

## やること

1. `README.md`
   - 「Bash のコマンドは実行される部分だけを見る」: 例に `echo "$(git push origin main)"`（止まる）、
     `grep -n "\$(git push)" f`（通る）、改行とプロセス置換を足す。「`$( )` の中は実行される」を引用の有無に
     よらない書き方にする。予約語（`coproc` を含む）の後ろがコマンドの先頭になること
   - 「読み切れないとき」の表: `unterminated-substitution` と `ambiguous-substitution` の行
   - 引用の中から切り出したコマンドに当たったときの断りと、回避策（単一引用、`\$(` と `` \` ``、`commit -F`、
     `--body-file`、値を出して読んでから書く 2 手）
   - 記録の欄と「試験の JSON」に `quoted`（当たったときだけ出る）
2. `ccnavi.md`
   - §6.3 の読み方: 走査の段を足し、heredoc の本文を落とすのを走査に移し、置換の中身を外側の後ろにつなぐ形に直す。
     予約語。記録の `quoted`
   - §12.2 許容する誤検知: 設計 §3「増える誤検知と回避策」の行と、敵対的レビューで残した 2 つ（`case` の語、大量の置換の遅さ）。
     ブレース展開を限界に 1 行
   - ADR-0001（字句は shlex に任せる）: 走査を足した理由（設計 §1.1）を追記するか、ADR を足すか、書いてみて決める
3. `HANDOVER.md`
   - heredoc の本文を行番号で落とす説明を、走査で落とす説明に直す

## 範囲

`README.md`、`ccnavi.md`、`HANDOVER.md` だけ。`requirements.md` は外から観測できる約束が変わる行があれば報告する
（親の範囲には入っているが、このフェーズでは触らない）。`.ccnavi/common/rule-samples.yml` への見本の追記は、
組み込みのルールで守られていてエージェントは書けないので、利用者に依頼したまま（下書きは渡してある）。
