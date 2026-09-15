---
version: 1
ticket: approve-carry-09
parent: approve-carry
phase: 5
title: 文書を書く（承認済みチケットを運ぶ sh、範囲の超過を判定で止める、承認の指紋）
rationale: '設計 `wip/design/approve-carry.md`（staging で追記した版）の決定を、README・設計書 ccnavi.md・
  要件 requirements.md に写し、ADR を足す。承認済みチケットを運ぶ ccnavi-push-approved.sh とボードの端末送り、 範囲の超過を承認で警告にして判定で止めること、ボードの承認の指紋（--digest）、phases.yml
  の読み込みの例外で 判定を落とさないこと、止める式の大文字小文字、--lint で超過が warn になることを書く。

  '
human_review:
  required: true
  reason: 外から見える約束（要件と承認の JSON の形）を書き換えるため
allow:
- match: Write|Edit
  glob: README.md
- match: Write|Edit
  glob: ccnavi.md
- match: Write|Edit
  glob: requirements.md
- match: Write|Edit
  glob: docs/*
started_at: 2026-09-14T21:50:29+0900
completed_at: 2026-09-14T22:00:28+0900
base_sha: 7bfc5e7bd243a9584a7e7ca2c25e160935d47ba7
ccnavi_approved:
  approved_at: 2026-09-14T21:46:10+0900
  source_tree: approve-carry
  source_path: /Volumes/Data/git/ccnavi/.claude/worktrees/approve-carry/wip/tickets/todo/approve-carry-09.md
cancelled_at: ''
cancel_reason: ''
---

# 文書を書く

## 入力

- 設計 `wip/design/approve-carry.md`（§1〜§8）
- MR #29 の敵対的レビューの記録（決定の理由）
- 今の README.md / ccnavi.md / requirements.md（main を取り込んだ版。節番号は main の今の番号に従う）

## 成果物

- README.md
  - スクリプトの一覧に `.ccnavi/scripts/ccnavi-push-approved.sh`（承認済みチケットだけをコミットして親のブランチへ push する。人が打つ。ボードは承認のあと端末に送る）
  - 承認の流れ: 端末の `ccnavi-approve.sh` とボードの承認が、どちらも運ぶ sh を通ること。ボードは Enter まで送ること
  - 「承認の JSON」: `--preview` の `digest` と `batch[].overflow[]`、`--yes` の `--digest`、`mismatch.digest`、`--digest` が無いときの誤り、`rejected[]` に載るのは形の壊れたものだけになったこと
  - `--lint`: 子の範囲の超過が warn になったこと（CI で落ちなくなる）
  - 環境変数 `CCNAVI_TICKETS_APPROVED` を運ぶ sh も読むこと
- ccnavi.md
  - §9.4（承認）: 運ぶ段、ボードの指紋の照合（本文と承認済みチケットに写る中身、部分ごとの指紋の並び）、承認画面の issue
  - §9.5（判定への効かせ方）: 表に「範囲の中だが種類の上限の外 → 止まる（DENY_TICKET_SCOPE）」、`limit:` 行、種類が読めないときの注記、phases.yml の読み込みの例外で判定を落とさないこと、止める形に `ccnavi-push-approved.sh` と大文字小文字
  - §9.7（フェーズの種類と計画）: `scope` の「効く場所」を「承認（警告）、判定」に
- requirements.md: REQ-TKT-04 と REQ-TKT-30 を「承認を拒む」から「承認画面で示し、判定で止める」に書き換え、設計 §8 の候補を REQ として足す。受入テストの表の対応を直す
- docs/adr: ADR を 1 本足す（範囲の超過を承認で止めず判定で止める／ボードの承認の指紋／運ぶ sh を分けてエージェントから止め、端末の検査は入れない）。ADR-0023 との関係（承認済みチケットと提案の指紋照合をやめた決定とは目的が違うこと）を書く

## やらないこと

- コードとテスト、`wip/design/` には触らない
- CLAUDE.md（エージェント向けの手順は変わらない）
