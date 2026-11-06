// Package stopreview は Stop の AI 判定（agent 型 Stop フック）の記録を持つ。判定計画と試行、
// approval-request の操作実績、判定役に渡す材料、判定結果の検証と最終分類、評価の集計。
//
// LLM は呼ばない。判定役が出した結果の構造だけを確かめ（指摘が正しいことは保証しない）、基準ごとの結果から
// Stop 全体の最終分類を一意に作る。試行に最終分類を書くのは、判定の結果なら Accept、候補でない・期限切れ・
// 読めないなら ccnavi（WritePlan・ExpireOverdue）だけで、状態は transitions の遷移に限る。
//
// 置くもの（state の置き場）: 同じ Stop の判定計画 `stopreview-plan-<キーの要約>.json`（同じキーへの
// 書き込みは冪等）、試行 `stopreview-attempt-<試行ID>.json`、操作実績 `stopops-pre-*`・`stopops-op-*` と
// 結果ファイル `stopops-result/*.kv`（ccnavi-ticket.sh が出口で書く）、セッションごとの `stopturn-`・
// `stoprefs-`・`stopwrite-`・`stoplast-`・`stoptaint-`。試行のイベントは `logs/stop-review/events.jsonl` に足し、
// 判定の記録（decisions.jsonl）には書かない。
//
// Python からの対応: records.stopreview・stopops・stopeval をまとめた。configure_log と state を引数に取る
// 関数は Store のメソッドにした（Logs が空ならイベントを書かない）。stopeval.render(summarize(…),
// unlinked_total(…)) → Report。load_criteria → LoadCriteria（出どころは誰も読まないので返さない）。
// save_material の抽出器の版は常に同じ値なので引数から外した。stopops.unlinked は集計の総数に寄せた。
// JUDGE_LAUNCHER → config.Launcher。
package stopreview
