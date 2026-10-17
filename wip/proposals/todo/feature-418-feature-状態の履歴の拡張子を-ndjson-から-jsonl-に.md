---
version: 1
ticket: feature-418-feature-状態の履歴の拡張子を-ndjson-から-jsonl-に
issue: 418
mr: 421
title: "[Feature] 状態の履歴の拡張子を .ndjson から .jsonl に揃える"
phases:
  quick-work:
    kind: work
    title: 小さい作業
    review: mr
    scope: inherit
    when: 変更が 1〜2 ファイルに収まり、設計判断や並行作業が要らないとき
plan:
  - quick-work
feedback: []
rationale: |
  状態の履歴だけ拡張子が .ndjson で、ccnavi の他の JSON Lines の記録（logs/decisions.jsonl、ccnavi-index.jsonl）は
  .jsonl を使っている。同じ形式で拡張子が 2 種類あり、読み手が迷う。.jsonl に揃える（issue 418）。
allow:
  - match: Write|Edit
    glob: "src/*"
  - match: Write|Edit
    glob: "tests/*"
  - match: Write|Edit
    glob: "src/ccnavi/tickets/**"
  - match: Write|Edit
    glob: "tests/**"
  - match: Write|Edit
    glob: "extensions/**"
  - match: Write|Edit
    glob: "docs/**"
  - match: Write|Edit
    glob: "README.md"
  - match: Write|Edit
    glob: ".ccnavi/docs/projects.md"
  - match: Write|Edit
    glob: ".gitignore"
---
## 背景

issue 418。ADR-0086 は、`*.jsonl` を無視するリポジトリで履歴ファイルが git add から漏れるのを避けるため `.ndjson` を選んだ。
ユーザの判断（issue 418 のやり取り）で、他のリポジトリの `.gitignore` への手当ては考えない。互換も持たない。

## 改版の内容

フィードバック計画を `feedback: []`（対応なし）で出す。フェーズ 1 のレビューは済み、未解決の指摘は無かった（`confirm` が通った）。
ほかは変えない。

## 計画の理由

フェーズ 1 はレビュー済みで、対応するフィードバックが無いため、空のフィードバック計画で親を閉じる。
