// Package closeout は、親を閉じる操作を置く。含むものは次の 2 つである。`ccnavi review ready`（と `--check`）は、
// Draft を外す前に行う。`ccnavi --close-early` は、ユーザがターミナルで打つ早めのクローズである（sh は
// ccnavi-user-close-early.sh）。ネットワークには出ない。Draft を外す・返信して解決する・issue を作るのは sh が行う。
// ここは、その前提を確かめ、マーカーと下書きを置く。
//
// sh との契約（変えない）は次のとおり。ready の標準出力は、1 行目がコメントの下書きのパス、2 行目が
// `tree <退避したツリーのルート>` である。wip/ から消したものがあれば、`wip-from <消す前の HEAD>` と
// `wip <消したパス>`（1 行 1 本）が続く。足すのは後ろの行だけにする。下書きのファイル名（readyFile・earlyIssueFile・
// earlyNoteFile）と目印も、sh が拾う。ready の前提は readyGate の 1 か所にあり、本体と `--check` が同じものを通る。
//
// Python からの対応: tickets/review/close をこのパッケージにした。ready → Ready、ready_gate → 私的な readyGate、
// check_ready → CheckReady、close_early → CloseEarly（plan_out・plan_done・plan_check・no_issue は EarlyOptions）、
// Leftovers → 私的な leftovers（nothing はメソッド）、READY_FILE・CLOSE_EARLY_ISSUE_FILE・CLOSE_EARLY_NOTE_FILE →
// 私的な readyFile・earlyIssueFile・earlyNoteFile。_archive_closed・_archive_place_problem・_ready_note・
// _restarted_tree・_archived_parent・_ready_again・_done_problem・_write_close_plan・_leftovers・_show_leftovers・
// _settle・_close_early_drafts は私的な関数のまま移した。home_dir が NoParentWorktree を投げる所は
// *approval.NoParentError を返す（入口が受ける）。
package closeout
