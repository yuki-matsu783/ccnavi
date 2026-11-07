// Package place は、チケットの置き場の名前と、パスの文字列だけで決まる置き場の見分けを持つ。ここに集めるのは、
// 状態の名前（置き場のディレクトリ名そのもの）、範囲を適用しない置き場（提案・承認済みチケット・下書き）、
// 途中の作業の置き場、退避と承認済みの領域の下の名前である。ファイルも git も読まない。
//
// 下の段（records/prune・policy/selfguard・records/stopreview）も名前で見る置き場（ArchiveName・Handoff・WipRoot・Todo）
// がある。下の段は tickets を読めないので、名前を infra/config に置く。ここの定数は、それを指す。
//
// Python からの対応: ticket_model の状態（TODO・REVIEW・DOING・DONE・CANCELLED・STATES・FINISHED・GUARDED_STATES）→
// State と States・Finished・GuardedStates、ticket_fold.progress → State.Progress、ticket_places の
// is_ticket_place・in_wip・leaves_open_state・lands_in_finished_state・is_handoff_place・is_unscoped・scratch_dir
// → IsTicketPlace・InWip・LeavesOpenState・LandsInFinishedState・IsHandoffPlace・IsUnscoped・ScratchDir、
// os.path.join(tree_root, *tickets.split("/")[, state]) → ProposalsRoot・Dir、
// HANDOFF・SCRATCH_ROOT・WIP_ROOT → Handoff・ScratchRoot・WipRoot、archive の ARCHIVE_DIR・PHASES_DIR・FLOWS_DIR・
// READY_DIR・PLACE → ArchiveDir・PhasesDir・FlowsDir・ReadyDir・ArchiveName、ticket_guard.OPS_REL → OpsRel。
package place
