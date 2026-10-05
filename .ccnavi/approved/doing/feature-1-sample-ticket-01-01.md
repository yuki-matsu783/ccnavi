---
version: 1
ticket: feature-1-sample-ticket-01-01
parent: feature-1-sample-ticket
phase: 1
human_review:
  required: false
  reason: 調査フェーズでレビュー不要の設定、かつサンプルのため
title: サンプル調査
rationale: |
  調査フェーズの子のサンプル。
allow:
  - match: Write|Edit
    glob: "wip/research/sample-ticket"
---
調査フェーズのサンプル。成果物は wip/research/sample-ticket/ に置く想定。実際には作らない。
