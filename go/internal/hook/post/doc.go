// Package post はツール実行後チェック。走ったあとのワークツリーを git に聞き、保護領域（ルールの deny と ask で書き込み系の
// ツールを含むもの）とチケットの範囲の外が変わっていないかを見る。実行前チェックは引数しか見ないので、ビルドの出力や
// スクリプトが内部で開くファイル、読めなかったシェルの書き込みは、ここでしか見つからない。止められないイベントなので、
// 返すのは戻す手順つきの文だけにする。戻す設定（CCNAVI_RESTORE_IF_DENY）が有効なら、deny の場所だけを git から戻す。
//
// 前から在った変更を直前の実行のせいにしない。セッションの始まり（CaptureBaseline）とターンの始まり（AtPrompt）に在ったものを
// 基準として記録し、基準に無い汚れだけを原因付きで報告する。ターンの終わり（AtStop）は、このターンで変わった保護領域
// （コミットに入ったものを含む）を、ユーザへ 1 度で見せる。ccnavi のサブコマンドがチケットの置き場に書いたと内容から読める変更と、
// integrate-done の取り込みと git で確かめたコミットは外す。
//
// state に置く記録（`<セッション>.reported.json`・`.accepted.json`・`.turn.json`）は records/prune が名前で消す。
//
// 取り込み済みのパスと squash の確かめは integrate の責務なので、tickets/lifecycle/integrate の SquashedPaths にある。
package post
