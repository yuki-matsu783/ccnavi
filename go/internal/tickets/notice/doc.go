// Package notice は、セッションの開始（SessionStart）でモデルとユーザに渡す知らせを組む。知らせは次のとおり。
// 直接作業のワークツリーの一覧（DirectWorktrees）、学びの提案の候補（Learning）、この PC で作業中の親の一覧（Parents。
// `ticket status` とボードも同じ行を使う）、終わっていない integrate の記録（PendingIntegrate）、scratchpad/ の容量
// （Scratchpad）、承認済みチケットの要求のうち上位に却下されたもの（Rejects。`--explain` も同じ走査を使う）。
//
// どれも見せるだけで、判定は変えない。読むのは手元のファイルと git の ref だけで、ネットワークには出ない。知らせを
// 組めなければ空を返し、セッションの開始は止めない。期限（deadline）は単調時計（monotonic）の絶対時刻で渡す。
// 期限を過ぎてから git を起こすことはしない。
package notice
