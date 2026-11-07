// Package post はツール実行後チェック。走ったあとのワークツリーを git に聞き、保護領域（ルールの deny と ask で書き込み系の
// ツールを含むもの）とチケットの範囲の外が変わっていないかを見る。実行前チェックは引数しか見ないので、ビルドの出力や
// スクリプトが内部で開くファイル、読めなかったシェルの書き込みはここでしか見つからない。止められないイベントなので、
// 返すのは戻す手順つきの文だけで、戻す設定（CCNAVI_RESTORE_IF_DENY）なら deny の場所だけを git から戻す。
//
// 前から在った変更を直前の実行のせいにしない。セッションの始まり（CaptureBaseline）とターンの始まり（AtPrompt）に
// 在ったものを基準として記録し、基準に無い汚れだけを原因付きで報告する。ターンの終わり（AtStop）は、このターンで
// 変わった保護領域（コミットに入ったものを含む）をユーザへ 1 度で見せる。ccnavi のサブコマンドがチケットの置き場に
// 書いたと内容から読める変更と、integrate-done の取り込みと git で確かめたコミットは外す。
//
// state に置く記録（`<セッション>.reported.json`・`.accepted.json`・`.turn.json`）は records/prune が名前で消す。
//
// Python からの対応: hook の post・post_findings・post_report をまとめた。check → Checker.Check、at_stop →
// Checker.AtStop、at_prompt → Checker.AtPrompt、capture_baseline → Checker.CaptureBaseline（共通の引数 stderr・state_dir・
// mine・watched・scope・places・root は Checker の欄）。Watched・ScopeGuard はそのまま、places の組 → Places、
// REASON_WORKTREE_UNREADABLE・REPORT_LIMIT・CODE_TICKET_SCOPE → ReasonWorktreeUnreadable・ReportLimit・CodeTicketScope。
// post_report の文（_violation・_preexisting）と post_findings の照合（_findings・_script_writes・integrated_paths）は
// 私的にした（上の段は呼ばない）。
package post
