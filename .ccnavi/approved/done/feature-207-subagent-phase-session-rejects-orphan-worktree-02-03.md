---
version: 1
ticket: feature-207-subagent-phase-session-rejects-orphan-worktree-02-03
parent: feature-207-subagent-phase-session-rejects-orphan-worktree
phase: 2
title: "#203 セッション開始時に却下された要求を示す"
rationale: |
  REQ-SES-02。上位の設定が却下したチケットの要求を、要求・理由・出所つきでユーザに示す。
human_review:
  required: true
  reason: SessionStart の出力の形が変わり、判定に使う走査を新しく足すため
allow:
  - match: Write|Edit
    glob: "src/ccnavi/hook/events.py"
  - match: Write|Edit
    glob: "src/ccnavi/hook/reasons.py"
  - match: Write|Edit
    glob: "src/ccnavi/tickets/*"
  - match: Write|Edit
    glob: "tests/core/*"
  - match: Write|Edit
    glob: "tests/ticket/*"
started_at: "2026-10-12T03:52:41+0900"
completed_at: "2026-10-12T04:41:52+0900"
base_sha: "cf0d17b653bf75225aa8e748a55c87366632cf8d"
---
## 調べて分かったこと(情報源が設計の核心)

「却下された要求」を承認時に記録してはいない。承認はチケットの中身を変えず(ADR-0104。承認済みチケットは提案とバイト単位で同じ)、
超過は承認画面に出すだけで、どこにも残らない(`agree_screen` の「書き込めない場所」、`cand.overflow` は承認の瞬間の値)。
判定は、書き込みのたびに次を都度計算している: ルールの deny が先に止める(`rules` > チケット)、子 → 親 → フェーズ定義の上限の順で切り詰める(`phase_scope.scope_verdict`)。

したがって情報源は 3 つの再計算で、新しい記録は作らない(記録を足すと承認の不変性に反し、承認のあとにルールや `phases.yml` が変わったときに古い事実を示す)。

| 却下する側 | 出所の言い方 | 再計算の方法 |
|---|---|---|
| ルールの deny | ルールの id とレイヤー(`source`)、deny の message | チケットの allow/ask の項を `ticket.subset_problems` と同じ「前置 + 1 文字」のプローブにして、行き先のレイヤーのルール集合の deny に当てる |
| 親の範囲 | 親の識別子 | `ticket.subset_problems`(既存) |
| フェーズ定義の上限 | フェーズ定義の名前と `scope`(親の `phases:` の写し) | `phasetypes.types_of(parent)` を `pt.decide` に当てる(既存の `scope_verdict` と同じ) |

glob 同士の包含は一般には決められないので、プローブは近似になる(regex の項は実際のパスが無いので「確かめられない」と書く)。近似であることは文面で断る。

## 実装方針

- 新しい関数を `tickets/` に足す(例 `tickets/session_rejects.py`): `rejected(conf, root, payload) -> list[Reject]`。入力は cwd のツリーに結び付く承認済みチケットだけ。
  走査は `approval.scan` を 1 回、ルールは `ruleload.layer_for(conf, root, tree)` から(`decide_at_start` は実行前チェックと違って git を起こしてもよいが、期限(`deadline`)の残りを見て打ち切る)。
- `hook/events.py` の `decide_at_start`: チケット制御が有効でサブエージェントでないとき、上の結果を `texts` に足す。
  ユーザに見せるのは `hookio.write_context(..., system=...)` の `systemMessage`、モデルに渡すのは `additionalContext`。
- 件数と先頭の数件(3 件程度)だけ。残りは件数で言い、`ccnavi --explain` で引かせる(REQ-CMN-12 の「1 行の要点」)。
- 何も無ければ何も出さない。
- ワークスペースルートからの起動(cwd のツリーにチケットが無い)は、開いているチケットのうち却下のあるものの件数を 1 行で言う。

## REQ-SES-03

作らない(決定済み)。上限を超えた許可は判定時に切り詰められて効かず、REQ-SES-02 と区別が付かないため。#203 は REQ-SES-02 の実装で閉じる。

## 影響範囲

SessionStart の出力が増えうる(起動・再開・compact・clear のたび)。判定は変えない。走査の費用は、承認済みチケット数 × 項数 × ルール数。上限を超えたら打ち切り、打ち切ったことを言う。
