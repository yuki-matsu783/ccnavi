// Package integrate は、チケットに結び付かない直接作業のワークツリー（`.claude/worktrees/<名前>`、ブランチも同じ
// 名前）が `integrate` まで進んでいるかを見る。Stop の促し（DirectAtStop）、`ccnavi-git.sh integrate` が断る
// 「チケットに結び付く」判定（BoundReason）、コミット後の案内と SessionStart の一覧が使う直接作業の判定
// （DirectCheck）、統合先の名前（IntegrationName）を 1 か所に置く。促した側と integrate が断る側で判定が
// 食い違わないようにするため。git とファイルだけを読み、ネットワークには出ない。
//
// 1 本のツリーを調べた結果は Hit・Clear・Unknown の 3 つ。期限不足・git の失敗・決められないものは Unknown で、
// 候補を外さず、促した記録も残さない。期限（deadline）のゼロ値は期限なし。
//
// Python からの対応: tickets/lifecycle/stop_integrate を移した。direct_at_stop → DirectAtStop、
// probe → 私的な probe、direct_record → DirectRecord（"_common"・"broken" の欄は Record の Common・Broken）、
// marker_kind → MarkerKind、ahead_since → AheadSince、integration_name → IntegrationName、
// _integration → Integration、bound_reason → BoundReason、bound_check → BoundCheck、direct_check → DirectCheck、
// has_record → HasRecord（dirty の None は nil）、_dirty → Dirty、_git_path → GitPath、_read_record → ReadRecord、
// _expired → Expired、_tree_roots → TreeRoots、_named_file → NamedFile、integrate_nudge → IntegrateNudge、
// blocked_notice → BlockedNotice、nudged_before・remember_nudge → stop.NudgedBefore・RememberNudge（Direct が
// stop.Nudge を満たす）、RECORD_DIR・DONE_DIR → RecordDir・DoneDir、HIT・CLEAR・UNKNOWN → Hit・Clear・Unknown。
package integrate
