---
version: 1
ticket: shellread-subst-02
parent: shellread-subst
phase: 2
predecessors:
- shellread-subst-01
title: コマンド置換・改行・プロセス置換を読んだあとの読みと判定を受入テストに書く
rationale: |
  設計（wip/design/shellread-subst.md）の §3 と §4 に、変更後の「shell の実測 → 切り出すか」
  「入力 → text / bare」「見本 → 判定」の表がある。それをそのままテストに書く。
  実装（3 番目）はこのテストが通ることを完了の条件にする。レビューは 3 番目と一緒に見る。

  設計の途中で、同じ種類の穴を main に見つけ、利用者の判断（2026-09-14）で範囲に含めた。
  改行がコマンドの区切りにならない、プロセス置換の中身が独立しない、語の途中の `#` から
  後ろが捨てられる、予約語の直後がコマンドの先頭にならない、の 4 つ（設計 §0）。
  うち 3 つは今 allow が後ろのコマンドまで通す。親チケットの本文には無いので、ここに書く。

  実装前に書くので、そのままでは落ちる。足すテストは、shellread に
  `REASON_AMBIGUOUS_SUBST` が無い間は skip し、実装が入った時点で効くようにする。
human_review:
  required: false
  reason: テストの追加だけ。実装と一緒に 3 番目でレビューする（延期）
allow:
- match: Write|Edit
  glob: tests/*
---

# 受入テスト: コマンド置換・改行・プロセス置換を読む

設計は `wip/design/shellread-subst.md`。

## 書くもの

1. `tests/test_shellread.py` に足す
   - §4.1 の 65 形。shell を走らせずに、表の「試作の読み」を期待値にする
     （切り出す = `M` が `(^|\x00)` の直後に立つ、切り出さない、縮退と理由）。
     「引用の中 / 外」は `bare` に `M` が残るかで確かめる
   - §4.2「入力 → 読み」の表。`␀` は `\x00`、`␁` は `\x01`
   - 閉じない置換（`echo $(git push`、`` echo `git push ``）が `unterminated-substitution`、
     `$( )` の中の `case` と 16 段を超える入れ子が `ambiguous-substitution` になること
2. `tests/test_acceptance.py` に足す（`.ccnavi/common/rules.yml` と組み込みのルールで判定する既存の書き方に倣う）
   - §3「変わるべきもの」「変わってはいけないもの」「増える誤検知と回避策」の見本と判定。
     回避策の綴りが止まらないことを含める
   - §4.3 の 3 つ（引用の中の断りが出る / 出ない、記録の `quoted`、縮退の断りの文が理由ごとに違う）
3. ゲートの免除（`phase.exempt`）とサブエージェントに許さない形（`phase.forbidden`）
   - `sh .ccnavi/scripts/ccnavi-git.sh commit -m "$(cat f)"` が免除されないこと
   - `sh .ccnavi/scripts/ccnavi-ticket.sh done x⏎sh .ccnavi/scripts/ccnavi-git.sh status` が免除されること
   - `echo "$(sh .ccnavi/scripts/ccnavi-ticket.sh done x)"` がサブエージェントに許されないこと

## skip の条件

`unittest.skipUnless(hasattr(shellread, "REASON_AMBIGUOUS_SUBST"), "shellread-subst の実装待ち")` を、
新しく足すテストのクラスかメソッドに付ける。既存のテストには付けない。
既存の `test_引用された記号を止めるのは許容した誤検知` の説明の書き直しは、実装（3 番目）で行う。

## 範囲

`tests/` の下だけ。`ccnavi/` と `.ccnavi/common/rule-samples.yml` は触らない。
