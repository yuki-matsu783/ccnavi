// Package events は hook の 1 回の起動を、イベント名から下の部品へ振り分ける入口。実行前チェックは judge、
// サブエージェントの始まりと終わりは subagent、実行後チェックは post が本体を持ち、ここはイベントごとに部品を
// 決まった順に呼び、記録（audit.Record）と state と応答をつなぐ。イベント 1 つにつき関数 1 つにして、どのイベントで
// 何が起きるかを上から読めるようにする。
//
// 止めるのは PreToolUse（judge）と、Stop の促し（finish・依頼までの道・フィードバック計画・サブエージェントの結果・
// 直接作業の integrate・`match: Stop` のルール。1 回の Stop で止める理由は 1 つだけ）と、PostToolUse の差し戻しだけ。
// ほかのイベントは文を足すだけにする。
//
// 想定外の失敗（下の部品の error・応答を書けない）は error で返し、入口（entry）が内部エラーの応答にする。panic は
// 受けない（Python が各所で except して空を返していた記録・案内の失敗は、Go の部品が error を返さず空にするので、
// 受ける所が無い。残る panic は不具合として入口がまとめて受ける）。
//
// Python からの対応: hook/events。decide → Decide。watch_context・watched_for・scope_guard・stop_rules_nudge と
// イベントごとの decide_* は私的にした（上の段が呼ぶのは Decide だけ）。PENDING_SECONDS → config.IntegratePending、
// CODE_RULE_NUDGE は私的な定数。_track_proposals・_plan_stop_review・_parents_at_start・_prune_at_start と
// decide_after の direct_candidates.touch を包んでいた except は持たない（呼ぶ部品が error を返さない）。
package events
