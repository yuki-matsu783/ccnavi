---
version: 1
ticket: feature-207-subagent-phase-session-rejects-orphan-worktree-03-01
parent: feature-207-subagent-phase-session-rejects-orphan-worktree
phase: 3
title: 4 件の変更に合わせて要件・設計・用語集を直す
rationale: |
  実装した挙動と要件・設計の文面を揃える。REQ-SES-03 を要件書から外し、局面の名前を実装の「クローズ可」に合わせる(どちらもユーザが決定済み)。
human_review:
  required: true
  reason: 要件書と設計書の変更を含むため
allow:
  - match: Write|Edit
    glob: "docs/requirements/*"
  - match: Write|Edit
    glob: "docs/design/*"
  - match: Write|Edit
    glob: "docs/CONTEXT.md"
  - match: Write|Edit
    glob: "README.md"
started_at: ""
completed_at: ""
base_sha: ""
---
## 直す文書

- #203(REQ-SES-03 を外す): `docs/requirements/session-start.md` の REQ-SES-03 の行を、REQ-SES-04 の先例に従って「欠番」の行にする。
  理由は「上限を超えた許可は判定時に切り詰められて効かず、REQ-SES-02 と区別が付かないため作らない」。
  表の下の REQ-SES-03 の説明の段落(「却下された要求は実害が出ないが…」)は削る。
  `docs/requirements/acceptance.md` の受け入れ条件 2 から REQ-SES-03 と「想定範囲外への許可」を外す。
  着手時にもう一度 `grep -rn "SES-03\|想定作業範囲\|想定範囲外" docs README.md` で、参照が残っていないか確かめる。
- #203(REQ-SES-02): `docs/design/startup-config.md` の `SessionStart` の行に却下の提示を足し、情報源(承認時の記録ではなく再計算)を書く。
- #207(局面の名前): `docs/requirements/tickets.md` の REQ-TKT-35、`docs/design/tickets/phases.md`(局面の名前の節)、`docs/CONTEXT.md`(局面の語)、
  `README.md`(「親の局面…を名指しするのは `--explain` とボードだけ」の段落。SubagentStart も名指しするようになる)で、
  「閉じられる」系を実装の「クローズ可」「クローズ可（フィードバック対応済み）」に合わせ(早めに閉じた場合は実装どおり「閉じられる（ユーザが早めに閉じた）」)、
  「全体計画待ち」を一覧から外す。`docs/design/tickets/subagents.md` に SubagentStart が親の局面を 1 行渡すことを書く。
  「親を閉じられる」(動詞の用法。`close.md`・`review.md` など)は局面の名前ではないので直さない。
- #210: `docs/design/multi-repo/trees.md`(11.3)に「元のプロジェクトが無いワークツリー」の定義と、判定は変えず `--lint` / `--explain` が名指しすることを書く。`docs/design/diagnostics.md` の `--explain` の一覧に節を足す。
- #205: `docs/design/limits.md` の期限の行に、実測の数字とその出し方(`tests/core/bench_deadline.py`)を書く。`docs/design/startup-config.md` の期限の段落から参照する。

## 注意

要件書の文言を変える作業(REQ-SES-03 の削除、REQ-TKT-35 の局面の一覧)を含む。どちらもユーザが決定済みだが、フェーズのレビューで差分を確かめてもらう。
