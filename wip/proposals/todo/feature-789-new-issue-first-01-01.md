---
version: 1
ticket: feature-789-new-issue-first-01-01
parent: feature-789-new-issue-first
phase: 1
title: 案内を「--new-issue を先に試す」に直す
human_review:
  required: true
  reason: 案内の方針と --check の出力が変わるため
allow:
  - match: Write|Edit
    glob: ".ccnavi/CCNAVI.md"
  - match: Write|Edit
    glob: ".ccnavi/docs/**"
  - match: Write|Edit
    glob: ".ccnavi/messages/**"
  - match: Write|Edit
    glob: "tests/**"
  - match: Write|Edit
    glob: ".ccnavi/scripts/ccnavi-start.sh"
  - match: Write|Edit
    glob: ".agents/docs/script-output.md"
  - match: Write|Edit
    glob: ".agents/docs/message-migration.md"
---
親の「範囲」の 1〜3 を実装し、「完了の確かめ方」のテストを足す。
