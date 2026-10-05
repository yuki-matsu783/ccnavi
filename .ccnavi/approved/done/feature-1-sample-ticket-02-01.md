---
version: 1
ticket: feature-1-sample-ticket-02-01
parent: feature-1-sample-ticket
phase: 2
predecessors: [feature-1-sample-ticket-01-01]
human_review:
  required: true
  reason: 承認後のレビュー待ちの表示も確かめるため
title: サンプル設計
rationale: |
  設計フェーズの子のサンプル。調査の子が閉じてから着手できる。
allow:
  - match: Write|Edit
    glob: "wip/design/sample-ticket"
started_at: "2026-10-06T07:00:52+0900"
base_sha: "39b749b3d72c2c8b3d8cfd2c3c5a2c6a641cdbe9"
completed_at: "2026-10-06T07:02:38+0900"
---
設計フェーズのサンプル。wip/design/sample-ticket/ に書く想定。実際には作らない。
