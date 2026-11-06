---
version: 1
ticket: feature-785-allow-whitespace-01-01
parent: feature-785-allow-whitespace
phase: 1
title: allow の空白を弾き、承認画面を箇条書きにする（実装）
human_review:
  required: true
  reason: 提案の検査（止める条件）と承認画面の表示を変えるため
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

親の提案「範囲」のとおりに、空白入り glob の検査と承認画面の箇条書きを実装し、試験と文書を足す。

## 範囲

親の提案と同じ。

## 完了の確かめ方

親の提案の「完了の確かめ方」のとおり。
