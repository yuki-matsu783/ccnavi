// Package notice は、セッションの開始（SessionStart）でモデルとユーザに渡す知らせを組む。知らせは次のとおり。
// 直接作業のワークツリーの一覧（DirectWorktrees）、学びの提案の候補（Learning）、この PC で作業中の親の一覧（Parents。
// `ticket status` とボードも同じ行を使う）、終わっていない integrate の記録（PendingIntegrate）、scratchpad/ の容量
// （Scratchpad）、承認済みチケットの要求のうち上位に却下されたもの（Rejects。`--explain` も同じ走査を使う）。
//
// どれも見せるだけで、判定は変えない。読むのは手元のファイルと git の ref だけで、ネットワークには出ない。知らせを
// 組めなければ空を返し、セッションの開始は止めない。期限（deadline）は Python の time.monotonic の絶対時刻にあたる。
// 期限を過ぎてから git を起こすことはしない。
//
// Python からの対応: tickets/notice の 6 つをまとめた。direct_worktrees.session_text → DirectWorktrees、
// learning_notice.notice → Learning（state_path・read_seen・decide・message は私的）、
// overview.collect・line・child_states・remote_info・session_text → Collect・Line・ChildStates・RemoteInfo・
// Parents（Row と CHILD_* → Row と Child* の定数）、pending_integrate.pending_notice・repo_key →
// PendingIntegrate・RepoKey（`logs/direct-tmp` → DirectTmpDir）、scratchpad.session_text → Scratchpad、
// session_rejects.notice・find_all・explain_lines → Rejects・FindRejects・ExplainLines（Reject・Found は同じ名前、
// find_all の引数は RejectOptions）。引数は conf, root の順にそろえた。
package notice
