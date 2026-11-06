---
version: 1
ticket: feature-785-allow-whitespace
issue: 785
mr: 792
title: "fix: allow の空白区切り 1 項目を提案の検査で弾き、承認画面を箇条書きにする"
phases:
  quick-work:
    kind: work
    title: 小さい作業
    review: true
    scope: inherit
    skill: process-implement
    when: 設計判断や並行作業が要らないとき
plan:
  - quick-work
rationale: |
  issue #785。子の allow を空白区切りの 1 項目で書いた提案が、lint・approval-request --check・承認画面を通り、承認後に全編集が DENY_TICKET_SCOPE で止まる。承認を頼む前に止め、承認画面で範囲を項目ごとに読めるようにする。
allow:
  - match: Write|Edit
    glob: "src/ccnavi/tickets/model/*"
  - match: Write|Edit
    glob: "src/ccnavi/tickets/proposal/*"
  - match: Write|Edit
    glob: "tests/**"
  - match: Write|Edit
    glob: ".ccnavi/docs/*"
  - match: Write|Edit
    glob: "docs/21_design/tickets/*"
  - match: Write|Edit
    glob: "extensions/vscode/ccnavi-board/src/**"
---
## 目的

空白を含む glob を提案の検査で error にし、承認画面の範囲を項目ごとの箇条書きにする。

## 範囲

- `_entries`（`src/ccnavi/tickets/model/ticket.py`）で、allow・ask・deny の `glob` に空白（スペース・タブ）があれば error にする。lint・`approval-request`（`--check` を含む）・承認画面が共通で通る読み口なので、ゲートは 1 か所に置く。`regex` は対象外。
- `agree_screen.py` で、allow・ask・deny と子の「親の範囲」「定義の範囲」を項目ごとの箇条書きにする。ボードの承認画面はこの本文をそのまま並べるので同じ表示になる。拡張の `approvalBody` が箇条書きを崩さないかを確かめる。
- 試験、`.ccnavi/docs/proposal-format.md`、`ticket-flow.md` の拒否条件の一覧、設計書（`docs/21_design/tickets/`）に、条件と表示を反映する。

## 対象外

空白を含む旧形式を読む処理・別名（後方互換）。regex の検査。範囲の判定（`scope_verdict`）の変更。

## 完了の確かめ方

空白区切り 1 項目の allow の提案が、`--lint`・`approval-request --check`・`approval-request` 本体のすべてで error になり、何も送られない。承認画面とボード用 JSON の本文が項目ごとの箇条書きになる。追加した試験と既存の試験が通る。

## 計画の理由

設計判断はユーザと合意済みで、実装 1 本で足りるため quick-work の 1 ステップにする。
