// Package request は、レビューの依頼と、依頼の記録からレビューの状態を読む部品を置く。依頼の操作は
// `ccnavi review prepare|requested` と `prepare --check` である。ネットワークには出ない。マージリクエストの中身は、
// sh（`.ccnavi/scripts/ccnavi-review.sh`）が取ってきて `--result` の JSON で渡す。その形が sh との契約である。
//
// 依頼は 2 段に分かれる。Prepare が前提をすべて確かめ、投稿する本文を state の置き場に書く。sh が投稿した結果を受けて、
// Requested が依頼のマーカーを置く。前提の検査は gate の 1 か所にあり、本体と `--check` が同じものを通る。
// 残った指摘の決め方（decide）・親を閉じる操作（close）・コア（confirm）は、ここの部品を読む。部品は、依頼の後に動いたか、
// 結果の照合、未解決の数え方、子の決着とレビュー済みのマーカー、wip/ の片付けである。
//
// Python からの対応: tickets/review/request をこのパッケージにした。request_gate → 私的な gate、check_request →
// Check、prepare → Prepare、requested → Requested、_parent → Parent、_parent_any → ParentAny、
// _phase → PhaseOf、_parent_phase → ParentPhase、_result → LoadResult、_result_with_mr → ResultWithMR、
// _matching → Matching、matching_problems → MatchingProblems、review_problems → ReviewProblems、
// changes_requested → ChangesRequested、unresolved_for → UnresolvedFor、ledger_of → LedgerOf、
// _mark・_parent_mark → Mark・ParentMark、reviewed_mark → ReviewedMark、_settle_children →
// SettleChildren、settle_and_mark → SettleAndMark、thread_label → ThreadLabel、_redo_request → RedoRequest、
// is_sha → IsSHA、Moved・KIND_* → Moved・movedKind の定数、moved_detail・moved_since → MovedDetail
// （changed の None は listed が偽）、_moved_detail → MovedInTree、moved_explanation → MovedExplanation、
// wip_tracked → 私的な wipTracked、remove_wip → RemoveWip、_merge_problems → MergeProblems、_write_text →
// WriteDraft、RESOLVE_NOTE → ResolveNote。thread_key・thread_version は host.ThreadKey・ThreadVersion を直に使う。
// _moved_since_request・_seen_changes・_unseen_by_review・_first_line・_read_body・_resolve・_git は中に畳んだ。
// home_dir が NoParentWorktree を投げる所は *approval.NoParentError を返す（入口が受ける）。
package request
