---
version: 1
ticket: feature-1-sample-ticket-02-02
parent: feature-1-sample-ticket
phase: 2
predecessors:
- feature-1-sample-ticket-02-01
human_review:
  required: true
  reason: レビューで残った指摘への対応
title: フェーズ 2 のレビューの指摘に応える
rationale: 'フェーズ 2 のレビューで残った指摘に応える。ユーザが端末で起こした続きの子で、承認はその判断で済んでいる。

  '
allow:
- match: Write|Edit
  glob: wip/design/sample-ticket
started_at: ''
completed_at: ''
base_sha: ''
followup_of:
- feature-1-sample-ticket-02-01
---

## 引き継ぐ指摘

- https://github.com/yuki-matsu783/ccnavi/pull/247#discussion_r4189762158 .ccnavi/approved/phases/feature-1-sample-ticket/workflow.yml:3 テスト指摘
