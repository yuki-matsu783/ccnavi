// Package cli は実行ファイルの入口。引数を読み、組み合わせを検査し、経路ごとの処理（hook の判定・診断・チケットと
// レビューの操作・sh が聞くこと）へ振り分ける。Run は終了コードを返し、終了は呼び手（cmd/ccnavi）に任せる。
//
// 引数の文法・usage・エラーの文・終了コードは、拡張と sh との契約である。argparse の誤りは usage と
// `ccnavi: error: …` を出して 1（Python の SystemExit を受けて 1 を返していた形）、`--help` の本文は usage/ の自前の文。
// hook の経路は、下の部品の error と panic を入口で受け、判定に達せなかった回として内部エラーの応答にする（終了コード
// 1 で抜けると Claude Code は PreToolUse の呼び出しをそのまま通す）。書く側の置き場（親のワークツリー）が決まらない
// error（*approval.NoParentError）は、ほかのコピーへは書かないので止める。
//
// Python からの対応: entry の cli・cli_args・cli_usage・cli_text・cli_ops・cli_handoff・cli_idle・cli_learnings・
// cli_mirror・cli_plan・cli_stopreview・cli_templates と __main__ をまとめた。run → Run、_run → run、_parsed → parsed と
// routes、_recorded_run → recordedRun、_hook・_hook_steps・_hook_crashed・_internal_error → runHook・hookSteps・hookCrashed・
// internalError、_resolve_switches → resolveSwitches、default_root → defaultRoot、cli_usage の OVERVIEW・USAGE・topic →
// overview・fullUsage・topic（本文は usage/*.txt）、cli_args の _override・_drop_outside_diagnosis・_not_with_docs・
// _from_terminal・_docs・_prune → override・dropOutsideDiagnosis・notWithDocsGiven・fromTerminal・runDocs・runPrune、
// cli_ops の operate・ops_command・sync_* → operate・opsCommand・sync*、各 cli_* の run → run<名前>。argparse の
// Namespace は opts が包み、Python と同じく経路の途中で値を差し替える。hookio.rebind_streams は持たない（Go は
// 標準入出力をバイト列のまま読み書きし、改行も書き換えない）。
package cli
