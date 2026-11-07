// Package phase は、親の計画のフェーズを組み、フェーズの終わりと HITL ポイント（ユーザのレビューを待つところ）を決める。
// フェーズは、承認済みチケットの置き場とマーカーから組む。
//
// 持つものは次のとおり。レビューが済むまでサブエージェントの起動とシェルを止めるゲート（Phase.GateClosed）、止めたときの
// 文（HoldReason）、終わったフェーズの 1 度きりの知らせ（Announce）、子の承認の順序（OrderProblems）、親の局面（Stage）、
// 手で done/ に動かした子の決着（AssessManual）。止めるかどうかの条件は Phase.GateClosed の 1 か所だけに置く。hook・
// ボード・Stop の促しは、それを読む。起動とシェルには書き込み先が無いので、止める親は cwd のワークツリーで引く（ParentAt）。
// 子の範囲の当て方は scope が持つ。
//
// Python からの対応: tickets/phase の gates と manual_settle をまとめた。gates: Phase の property → 同名の
// メソッド（risk → Risk、review_at の None → ok が偽、declared_review の None → ticket.Unsaid）、
// phases_of(proposed=None) → Phases、phases_of(proposed=…) → 私的な phasesOf、held_phase → HeldPhase、
// worktree_at → WorktreeAt、parent_at → ParentAt（parent_for_cwd は ParentAt の 1 つめ、parent_in は私的）、
// hold_reason → HoldReason、announce → Announce、downstream_touched・reopen_impact → ReopenImpact、
// reviewed_or_skipped → Phase.ReviewedOrSkipped、review_next → ReviewNext、resumed_review → ResumedReview、
// order_problems → OrderProblems（呼び手が渡しても読み直していた types の引数は持たない）、plan_finished →
// PlanFinished、stage → Stage、LABEL_* → LabelPreparing・LabelWaiting、CODE_* → CodeReview・CodeSubagent、
// PARALLEL_REQUEST_NOTE → ParallelRequestNote、STAGE_FEEDBACK_WAITING → StageFeedbackWaiting。
// review_venues は Phases の各 ReviewRequired で足りるので持たない。types_path・layer_types・load_types の
// 別名は phases.TypesPath・LayerTypes・LoadTypes を直に使う。TIMEOUT_SECONDS は scope が git.StateTimeout を使う。
// manual_settle: assess → AssessManual（external・moved_at → ManualOptions）、Manual・line → Manual・Line、
// NONE などの状態 → Settle の定数、MARGIN_SECONDS → Margin（time.Duration）、moved_at の epoch 秒 →
// Manual.MovedAt（time.Time）。
package phase
