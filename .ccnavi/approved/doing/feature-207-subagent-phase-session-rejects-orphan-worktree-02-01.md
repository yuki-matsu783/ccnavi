---
version: 1
ticket: feature-207-subagent-phase-session-rejects-orphan-worktree-02-01
parent: feature-207-subagent-phase-session-rejects-orphan-worktree
phase: 2
title: "#207 SubagentStart で親の局面を名指しする"
rationale: |
  REQ-TKT-35 の後半。サブエージェントが「親 X、フェーズ N: タイトル」に続けて、親が今どの局面にいるかを読めるようにする。
human_review:
  required: true
  reason: SubagentStart の文面が変わるため
allow:
  - match: Write|Edit
    glob: "src/ccnavi/hook/subagent.py"
  - match: Write|Edit
    glob: "src/ccnavi/tickets/phase.py"
  - match: Write|Edit
    glob: "tests/ticket/*"
started_at: ""
completed_at: ""
base_sha: ""
---
## 実装方針

- `hook/subagent.py` の `at_start`: 子の一覧を作った後、対象の親(`index.get(t.parent)` の重複を除いたもの)ごとに `phase.stage(root, conf, parent, raw)` を呼び、子の行の前に「親 X の局面: …」を 1 行足す。
  置き場の読みは `approval.read_raw(conf, root)` を 1 回だけ行って `stage` に渡す(`diagnose_explain.py` と同じ。二度読まない)。
- `stage` は `phases_of` を読むので I/O がある。例外を `at_start` の外に出さない(既存のフローの読みと同じく 1 行の知らせにして続ける)。
- 局面の名前は変えない(決定済み)。`phase.stage` の「クローズ可」はそのまま。要件・設計の「閉じられる」を実装に合わせるのは文書フェーズ。
- 「全体計画待ち」は `stage` が返さない(計画が無い親は空文字)。SubagentStart では起きないので足さない。
- 入れ子の孫(親のツリーからの起動)でも局面は同じ文で出る。局面は案内であって判定ではない。

## 影響範囲

- 変わるのは SubagentStart の `additionalContext` に 1 行足すことだけ。判定・範囲・承認は変わらない。
- 文字数: 親ごとに 1 行。子が多くても親は 1〜数件。フローの文字数の予算(`flow.TOTAL_TEXT_LIMIT`)には数えない。

## 触れない

`phase.stage` の判定(どの局面か)そのもの。
