---
version: 1
ticket: chore-3-small-pair-00-01
parent: chore-3-small-pair
phase: 0
human_review:
  required: true
  reason: 小さい作業を親 1 子 1 で回せるかの試し
title: 試し書きをする子
allow:
  - match: Write|Edit
    glob: "docs/*"
---
`docs/` に試し書きを 1 ファイル作る。
