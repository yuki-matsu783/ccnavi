// Package core は、取り返しのつかない 2 つの操作の入口を持つ。1 つめは承認（`--agree`。ターミナル・ボードの `--yes`・
// `--preview`・`--verify`）、2 つめはレビュー済み（`review confirm`）である。どちらも判定（agree.Gather・request の検査）
// を通る。書くものは fsio の溜める段に並べ（agree.PlanBatch・request.SettleAndMark）、ここの書き手（Writer(FS)）が
// ディスクに書く。並べた中で失敗したときの扱い（止める・言って続ける・行を出す）は、書き手の 1 か所で決める。
//
// 承認の JSON の形・ダイジェスト・終了コード（0・1・3・4）は、拡張（extensions/vscode/ccnavi-board の approvemodel.ts）
// と sh との契約である。通らなかった根拠は、文面の頭に `[CORE_*]` を付けて載せる。拡張と試験は、文の言い回しではなく
// この材料で分ける。親のワークツリーが決まらないとき（*approval.NoParentError）は error で返し、入口が受ける。
//
// Python からの対応: tickets/core（api・approve・base・review）のうち、入口（entry）が呼ぶものを移した。
// approve → Approve、preview → Preview、verify → Verify、approve_yes → ApproveYes、approval_request_gate →
// ApprovalRequestGate、readiness・Readiness → 私的な readiness、confirm_local → ConfirmLocal、check_confirm →
// CheckConfirm。judge_approval・Verdict → 私的な judge・verdict、plan・write_fs・_write_op → 私的な write
// （_write_op は fsio.Op.Apply）、write_recorded・written_paths・copy_paths・dirty_parents・placed_report・
// refusal_code・tagged・refused・confirm・confirm_gate・_precondition・_manual_refusal・_ledger_entry・
// next_after_confirm・_open_parent は私的。Snapshot・Actor・read_fs は畳んだ（時刻は fsio.Stamp、経路は via）。
// Chrome 拡張（entry/chrome の WASM）が引く名前は chrome.go と decide.go に置いた。judge_approval → JudgeApproval
// （答えは Judged）、plan → Plan、Changes.per_branch・lines → Changes.PerBranch・Lines、refusal_code → verdict.refusal、
// confirm → Confirm（答えは Checked）、reviewable → ReviewableOf、requested_head → RequestedHead、moved_detail_on_host →
// MovedDetailOnHost、manual_for → ManualFor、decide_preview → DecidePreviewOf、decide → Decide。moved_on_host は呼び手が
// 無いので移していない。
package core
