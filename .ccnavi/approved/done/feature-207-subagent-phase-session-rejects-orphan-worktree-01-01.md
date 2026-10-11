---
version: 1
ticket: feature-207-subagent-phase-session-rejects-orphan-worktree-01-01
parent: feature-207-subagent-phase-session-rejects-orphan-worktree
phase: 1
title: "#207 SubagentStart の局面の受入テスト"
rationale: |
  SubagentStart が親の局面を名指しすること(REQ-TKT-35 の後半)を、実装の前にテストで固定する。
human_review:
  required: true
  reason: 受入テストの期待する文面が、ユーザとサブエージェントに見える文言の仕様になるため
allow:
  - match: Write|Edit
    glob: "tests/ticket/*"
started_at: "2026-10-12T03:11:08+0900"
completed_at: "2026-10-12T03:46:41+0900"
base_sha: "8548fc14bfe6a07a792443d35f8c15550610d23f"
---
## テストの方針

`tests/ticket/test_subagent_stage.py`(新規)。既存の `tests/ticket/test_flow.py` の SubagentStart の組み立てを真似る。

- 親のツリーから起動: 子の行のあとに「親 X の局面: 作業中(2: 実装とテスト)」の形で 1 行出る
- 子のツリーから起動: 同じ行が出る(自分の親の局面)
- 局面が複数(並行する枝): 番号を並べた形がそのまま出る(`phase.stage` の文字列をそのまま使う)
- 計画の無い親: 局面の行を出さない(空文字)
- レビュー準備中/レビュー待ち/フィードバック計画待ち/フィードバック対応中/閉じられる(早めに閉じた場合も)の各文字
- 親が複数の子を持つとき局面の行は 1 回だけ(子ごとに繰り返さない)
- 局面を組む例外(読めない置き場など)で SubagentStart を失敗させない(既存の「フローを読めない」と同じ扱い)

局面の名前は変えない(決定済み)。期待する文字は実装どおりの「クローズ可」「クローズ可（フィードバック対応済み）」で、「全体計画待ち」は出ないことも確かめる。
