---
version: 1
ticket: feature-207-subagent-phase-session-rejects-orphan-worktree-01-03
parent: feature-207-subagent-phase-session-rejects-orphan-worktree
phase: 1
title: "#203 セッション開始時の却下の提示の受入テスト"
rationale: |
  REQ-SES-02 の出力を、実装の前にテストで固定する。REQ-SES-03 は作らないと決めたので、テストにも含めない。
human_review:
  required: true
  reason: SessionStart の出力の形が変わるため
allow:
  - match: Write|Edit
    glob: "tests/core/*"
  - match: Write|Edit
    glob: "tests/ticket/*"
started_at: "2026-10-12T03:11:26+0900"
completed_at: "2026-10-12T03:46:50+0900"
base_sha: "8548fc14bfe6a07a792443d35f8c15550610d23f"
---
## テストの方針

`tests/ticket/test_session_rejects.py`(新規)。`tests/core/test_session_conventions.py` の SessionStart の組み立てを真似る。

REQ-SES-02(却下された要求):
- 承認済みチケットの allow/ask の項がルールの deny に当たる: 要求(書かれた glob)・理由(deny の message)・出所(ルールの id とレイヤー)が出る
- 子の項が親の範囲かフェーズ定義の上限を超える: 出所は「親 X」「フェーズ定義 Y」
- 複数は件数と先頭の数件に絞り、「詳しくは --explain」を添える
- 却下が無ければ何も出さない(常駐の文脈を増やさない)
- cwd のツリーに結び付くチケットだけを見る。ワークスペースルートからの起動は件数の 1 行まで
- サブエージェントには出さない(SessionStart はそもそも来ないが `agent_id` 付きの経路を確かめる)
- 出力は `systemMessage`(ユーザに見える)と `additionalContext`(モデルに渡る)の両方。判定・終了コードは変わらない

期限: 追加の走査が `deadline` を食わないこと(チケットが多い置き場でも件数で打ち切る)。

REQ-SES-03 は作らない(上限を超えた許可は判定時に切り詰められて効かず、REQ-SES-02 と区別が付かないため)。
