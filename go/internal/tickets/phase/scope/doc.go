// Package scope は子チケットの範囲の当て方を持つ。書き込み先がどの上限（子自身・親・フェーズ定義）で外へ出るかの
// 判定（Of）と、子のワークツリーに残った範囲の外の変更の洗い出し（Findings・MergeFindings）。範囲を当てる場所
// （実行前チェック・実行後チェック・SubagentStop の差し戻し・finish の合流の前）はすべて Of を通す。別に書くと、
// 同じ書き込みが実行前は通って実行後に範囲外と報告される。承認は範囲の超過を警告で通すので、超えた分を止めるのは
// ここだけになる。
//
// Python からの対応: tickets/phase/scope。ScopeVerdict（verdict・limit・type・outside）→ Verdict（Decision・
// Limit・Type・Outside）、LIMIT_* → Limit の定数、scope_verdict → Of、plan_item → 私的な planItem、type_for →
// TypeFor、scope_findings → Findings（(パス, 判定) の組 → Finding、読めなかった理由 → error）、merge_findings →
// MergeFindings。git の期限は Python の phase.TIMEOUT_SECONDS と同じ 2 秒の git.StateTimeout。
package scope
