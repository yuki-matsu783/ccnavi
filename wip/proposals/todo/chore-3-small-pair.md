---
version: 1
ticket: chore-3-small-pair
human_review:
  required: true
  reason: 小さい作業を親 1 子 1 で回せるかの試し
title: 小さい作業の親子の試し
allow:
  - match: Write|Edit
    glob: "docs/*"
---
計画（`plan`）を持たない親に、`phase: 0` の子を 1 本つけて、`finish` から `chat` まで通るかを試す。
