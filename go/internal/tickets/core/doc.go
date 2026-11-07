// Package core は取り返しのつかない 2 つの操作、承認（`--agree`。ターミナル・ボードの `--yes`・`--preview`・
// `--verify`）とレビュー済み（`review confirm`）の入口を持つ。どちらも判定（agree.Gather・request の検査）を通り、
// 書くものを fsio の溜める段に並べ（agree.PlanBatch・request.SettleAndMark）、ここの書き手（Writer(FS)）が
// ディスクに書く。並べた中の失敗の扱い（止める・言って続ける・行を出す）は書き手 1 か所で当てる。
//
// 承認の JSON の形・ダイジェスト・終了コード（0・1・3・4）は拡張（extensions/vscode/ccnavi-board の
// approvemodel.ts）と sh との契約。通らなかった根拠は文面の頭に `[CORE_*]` で載せる（拡張・試験が文の言い回しで
// なく同じ材料で分ける）。親のワークツリーが決まらない（*approval.NoParentError）は error で返し、入口が受ける。
//
// Python からの対応: tickets/core（api・approve・base・review）のうち、入口（entry）が呼ぶものを移した。
// approve → Approve、preview → Preview、verify → Verify、approve_yes → ApproveYes、approval_request_gate →
// ApprovalRequestGate、readiness・Readiness → 私的な readiness、confirm_local → ConfirmLocal、check_confirm →
// CheckConfirm。judge_approval・Verdict → 私的な judge・verdict、plan・write_fs・_write_op → 私的な write
// （_write_op は fsio.Op.Apply）、write_recorded・written_paths・copy_paths・dirty_parents・placed_report・
// refusal_code・tagged・refused・confirm・confirm_gate・_precondition・_manual_refusal・_ledger_entry・
// next_after_confirm・_open_parent は私的。Snapshot・Actor・read_fs は畳んだ（時刻は fsio.Stamp、経路は via）。
// Chrome 拡張（Pyodide）だけが引く名前（Changes.per_branch・reviewable・requested_head・moved_on_host・
// moved_detail_on_host・manual_for・decide・decide_preview）は Go に呼び手が無いので移していない。
package core
