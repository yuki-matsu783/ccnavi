---
version: 1
ticket: feature-744-human-run-revise-approval-01-01
parent: feature-744-human-run-revise-approval
phase: 1
title: human_run の成否に親の改版の承認を含める
human_review:
  required: true
  reason: 受入シナリオの runner の成否の判定を変えるため
allow:
  - match: Write|Edit
    glob: "tests/scenarios/**"
  - match: Write|Edit
    glob: ".agents/docs/acceptance-scenarios.md"
started_at: "2026-10-31T04:35:20+0900"
base_sha: "d6c1fba31913c1e76e21cd699543b43661746af0"
---
`_run_human_run` の成否を、新しく承認済みになった識別子に加えて、打つ前から承認済みで、打ったあとに承認待ちから消えた識別子（親の改版）も成功と数えるよう直す。テストと `acceptance-scenarios.md` の記述を揃える。
