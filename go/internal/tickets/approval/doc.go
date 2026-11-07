// Package approval は承認済みチケットの置き場を持つ。判定が正とするのは、ここが読んだ承認済みチケットだけである。
//
// 提案はエージェントが書けるため、判定が提案を読むと範囲を書き足して通れてしまう。正とするのはチケットの欄ではなく
// 置き場である。`.ccnavi/approved/` は組み込みの保護でエージェントの書き込みが止まる。提案を手で doing/ へ動かすことも
// 承認の経路になる。この経路は承認の画面を通らないので、承認のときにしか当たらなかった構造の検査は、判定の側で
// blockingProblems（1 か所）が当て、理由を Ticket.Blocked に入れる。承認はチケットの中身を変えず、提案のバイト列のまま
// 動かす。先行関係はファイルに持たず、読むたびに計画の after から計算する（LoadCopy）。
//
// 置き場の読み（Scan・ScanReview・ReadRaw・ScanProposals など）、構造の検査（blockingProblems・FamilyProblems など）、
// 先行の状態（Pool・UnmetPredecessors・PredecessorProblems）、チケットを動かす・書く操作（Admit・moveFile・
// SettleReview・Followup など）、表示だけが読む承認の時刻（ApprovedTimes）を持つ。マーカーと記録は marks、
// write-and-push の見分けは wap にある。
//
// Python からの対応: tickets/approval の store・checks・ops・times をまとめた（大きさで分けていたため）。
// store: copy_path・closed_path → CopyPath・ClosedPath、DOING_DIR・DONE_DIR → place.Doing・place.Done、
// FOLLOWUP_KEY → FollowupKey、load_copy → LoadCopy、trees → Trees、scan_all・review_all → ScanAll・reviewAll、
// Raw・read_raw → Raw・ReadRaw（everything → Raw.Everything）、scan(closed=…, raw=…) → Scan(conf, root, closed, raw)、
// scan_review → ScanReview、scan_proposals・read_proposals → ReadProposals（答えは Proposals。stale_proposals の
// 答えは Proposals.Stale）、all_tickets → ReadRaw(…).Everything()（Scan・ScanReview は raw が nil なら同じ 3 つを
// 読む）、revision_elsewhere・revision_elsewhere_text → RevisionElsewhere・RevisionElsewhereText、plan_signature・
// phases_signature・scope_signature の != → SamePlan・SamePhases・SameScope、plan_differs → PlanDiffers、
// NoParentWorktree → ErrNoParentWorktree（*NoParentError）、home_dir → HomeDir、home_dir_read(project=…) →
// HomeDirRead、home_dir_read(project=None) → HomeDirReadAny、unreadable_copies の (パス, 説明) → Unread、
// UnreadableCopies、source_path → SourcePath、_authoritative → authoritative。
// checks: by_id → ByID、_by_id → ticket.ByTicket、children_of → ChildrenOf、family_standing → FamilyStanding、
// family_problems・mr_problems・branch_problems・place_problems・project_problems・child_problems・
// blocking_problems → 同名の *Problems、home_problem・misplaced_home_text → HomeProblem・MisplacedHomeText、
// resumed_fields → ResumedFields、mark_blocked・mark_imported → markBlocked・markImported（Scan が当てる）、
// PRED_* → PredState の定数、Predecessor（met・waiting・label → met・Waiting・Label）、predecessor_pool_of →
// PoolOf、align_imported → Pool.AlignImported、unmet_predecessors → UnmetPredecessors、predecessor_problems →
// PredecessorProblems。ops: admit・changed_since_read・update_fields・note_mr・close_copy・to_review・move_file・
// settle_review・next_child_id・existing_ticket_file・followup_problem・followup・predecessor_pool → 同名の関数
// （CHANGED_SINCE_READ は ChangedSinceRead の文に閉じた。settle_review と next_child_id の例外は error で返す）。
// times: approved_times → ApprovedTimes（use_git と Pyodide の分岐は呼び手が無いので持たない）、
// ApprovedTime・APPROVED_FROM_COMMIT・APPROVED_UNCOMMITTED → ApprovedTime・fromCommit・Uncommitted。
// 呼び手の無い copies・state_of・review_path・tree_path・stale_proposals・nowhere・outside_reason・
// family_stop_text・content_problems は公開しない。
package approval
