// Package subagent はサブエージェントの始まり（SubagentStart）と終わり（SubagentStop）を扱う。どちらも止められないイベントなので、
// 判定はしない。始まりには、cwd のワークツリーに関わる開いている子の案内を渡す。終わりには、子の範囲の外に残った変更を
// 1 回だけ差し戻す。差し戻しを無視して終わったこと・範囲を確かめられなかったことは、親の PostToolUse か UserPromptSubmit で
// 親に言う。サブエージェントの出力は親に届かないため。
//
// 担当の子が決まるのは、cwd が子のワークツリーのときだけ。親のツリーから起動された体は、どの子の担当か分からない。そのため、
// 始まりではフローを 1 行だけにし、終わりでは差し戻さずに親へ知らせる（outcome.Unattributed）。
//
// state に置く印は `subagent-<セッション>-<相手>` に .bounced（差し戻した）・.unchecked（範囲を確かめられなかった）・
// .running（実行中）を付けた名前で、records/prune が同じ名前で消す。セッションは名前に入れるときに字を置き換えるので、
// 前置きは候補を絞るだけにし、どのセッションの印かは中身の 1 行目で決める。
//
// Python からの対応: hook/subagent。at_start・at_stop → AtStart・AtStop（書けなければ error）、forget_running →
// ForgetRunning、running → Running、ignored_bounce・unchecked_scope → IgnoredBounce・UncheckedScope、
// outcome_notices → Notices、unchecked_after_error → AfterError、RUNNING_MAX_SECONDS → config.SubagentRunningMax。
// 写すだけだった confirm_outcomes・stop_lines は持たない（呼び手が outcome.Confirm・outcome.StopLines を読む）。
// CANDIDATE_NOTE は私的にした（読むのは CLI を通すテストだけ）。
package subagent
