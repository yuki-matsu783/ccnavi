---
version: 1
ticket: feature-1-sample-ticket
plan: [research, design]
feedback: []
title: サンプルチケット（承認フローと拡張機能の表示確認用）
rationale: |
  承認の流れと VS Code 拡張の表示を確かめるためのサンプル。実際の作業は行わない。
allow:
  - match: Write|Edit
    glob: "wip/research/sample-ticket"
  - match: Write|Edit
    glob: "wip/design/sample-ticket"
started_at: "2026-10-06T00:06:03+0900"
base_sha: "8ec045528cfbfcffa180b8018e6aa93a62e4500b"
completed_at: "2026-10-06T22:54:51+0900"
---
承認フローと拡張機能の表示を確かめるためのサンプルの親チケット。
子は 2 つ（調査・設計）。承認・着手・レビュー待ち・完了の各状態を順に確かめる。
