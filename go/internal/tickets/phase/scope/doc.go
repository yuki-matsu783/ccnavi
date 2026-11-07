// Package scope は、子チケットの範囲の当て方を持つ。含むものは次の 2 つである。1 つめは、書き込み先がどの上限
// （子自身・親・フェーズ定義）で外へ出るかの判定（Of）。2 つめは、子のワークツリーに残った範囲の外の変更の洗い出し
// （Findings・MergeFindings）。
//
// 範囲を当てる場所（実行前チェック・実行後チェック・SubagentStop の差し戻し・finish の合流の前）は、すべて Of を通す。
// 別々に書くと、同じ書き込みが実行前は通り、実行後に範囲外と報告される食い違いが起きる。承認は範囲の超過を警告で通す。
// そのため、超えた分を止めるのはここだけになる。
//
// Python からの対応: tickets/phase/scope。ScopeVerdict（verdict・limit・type・outside）→ Verdict（Decision・
// Limit・Type・Outside）、LIMIT_* → Limit の定数、scope_verdict → Of、plan_item → 私的な planItem、type_for →
// TypeFor、scope_findings → Findings（(パス, 判定) の組 → Finding、読めなかった理由 → error）、merge_findings →
// MergeFindings。git の期限は、Python の phase.TIMEOUT_SECONDS と同じ 2 秒の git.StateTimeout である。
package scope
