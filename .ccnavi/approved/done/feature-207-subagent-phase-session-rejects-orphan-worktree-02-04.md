---
version: 1
ticket: feature-207-subagent-phase-session-rejects-orphan-worktree-02-04
parent: feature-207-subagent-phase-session-rejects-orphan-worktree
phase: 2
title: "#203 --explain に却下された要求の一覧を出す"
rationale: |
  フェーズ 2 のレビューで決めた(判断 1 は A)。SessionStart の案内は却下を 3 件までしか見せず、残りは「全部は ccnavi --explain で」と指すが、--explain に一覧が無く行き止まりになっている。REQ-SES-02 の「ユーザに示す」を満たすため、--explain に一覧の節を足す。
human_review:
  required: true
  reason: --explain の出力の形が変わり、却下の走査を診断側からも呼ぶため
allow:
  - match: Write|Edit
    glob: "src/ccnavi/entry/diagnose_explain.py"
  - match: Write|Edit
    glob: "src/ccnavi/tickets/session_rejects.py"
  - match: Write|Edit
    glob: "tests/core/*"
  - match: Write|Edit
    glob: "tests/ticket/*"
started_at: "2026-10-12T04:53:46+0900"
base_sha: "1ae9858ffff4b6af8edf83ffe230e326a8af0aea"
completed_at: "2026-10-12T05:31:58+0900"
---
## やること

`ccnavi --explain` に、却下された要求を全件並べる節「■ 上位の設定に却下された要求」を足す。

- 情報源は、フェーズ 2 の 02-03 で足した `tickets/session_rejects.py` の再計算をそのまま使う。新しい記録は作らない。
  SessionStart 用の「先頭の数件 + 件数」への絞りと、予算(0.5 秒)の打ち切りは文面を作る側にあるので、全件を返す関数を切り出して、`notice` と `--explain` の両方が呼ぶ形にする。
- 1 件 1 行で、要求(glob と動作)・理由(message)・出所(ルールの id とレイヤー、親の識別子、フェーズ定義の名前のどれか)を出す。
- 近似であること(項の一部だけが退けられる形は拾わない)と、regex の項は確かめられない件数を、節の末尾に添える。
- 該当が無ければ節を出さない。`--explain --json` には欄を足さない(ボード側の読み手を触らないため)。
- SessionStart の案内文の「全部は ccnavi --explain で」は、そのまま正しくなる。文面は変えない。
- 判定は変えない。

## テスト

- `tests/ticket/test_session_rejects.py` に、`--explain` が全件を出すこと(12 件のとき 12 行)、1 行に要求・理由・出所が入ること、却下が無ければ節が出ないこと、`--explain --json` に欄が無いことを足す。
- 先に失敗することを確かめてから実装する。
- ruff を通し、`tests/ticket`・`tests/core` を回す。

## 影響範囲

`--explain` の出力が増えうる。`--explain --json` と判定は変えない。
