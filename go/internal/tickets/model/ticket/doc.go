// Package ticket はチケット 1 本の型と、その読み書きの中心を持つ。frontmatter の書式の定数、範囲の項・計画の
// ステップ・先行関係の型、frontmatter から型付きの Ticket を組む読み手、置き場の走査（Scan）、同じ識別子の
// まとめ方（Fold・Dedupe）、親子の判定の合わせ方、新規の親の識別子の検査。
//
// チケットはエージェントが書く提案であって正ではない。判定が使うのは承認済みチケットだけで、範囲は絞ることしか
// できない（子は親の部分集合で、親子は厳しい側を採る）。読み込みは、承認済みチケットを索引から落とさないため、
// 形の誤りの一部（after・phases・maintenance）を落とさずに持ち、承認と判定の側が言う。
//
// Python からの対応: ticket_model・ticket・ticket_fold・donecopy をまとめた。ticket_model の Entry・PlanItem・
// Workflow・Ticket → Entry・PlanItem・Workflow・Ticket（bool|None は Need、番号の None は 0、feedback の None は
// FeedbackPlanned が偽）、step_flags → StepFlags、chosen_review → ChosenReview、ticket.parse・load・scan・scan_all
// → Parse・Load・Scan・ScanAll、front_mapping → FrontMapping、subset_problems → SubsetProblems、combine → Combine、
// regex_overflow_detail → RegexOverflowDetail、glob_has_space → GlobHasSpace、issue_text → IssueText、issue_label →
// Ticket.IssueLabel、ticket_ids.branch_name_problems → Ticket.BranchNameProblems、ticket_fold の fold・authority・
// collided_states・collisions・by_ticket・dedupe → Fold・Authority・CollidedStates・Collisions・ByTicket・Dedupe
// （progress は place.State.Progress）、donecopy.copy_fields → CopyFields、ticket.render → Render（extra を渡す呼び手が
// 無いので受けない。書き出せない値は error）。
package ticket
