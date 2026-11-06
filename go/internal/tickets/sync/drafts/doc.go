// Package drafts は作業・レビュー計画のテンプレートの下書き。完了した親から作り（Make）、全ツリーの下書きを読んで
// 検査し（Scan）、親が閉じるときにテンプレート化を案内する（CloseNotice）。下書きの置き場はほかの保護対象と同じ
// wip/handoff/<取り込み先の相対パス> で、取り込みは handoff（ccnavi-user-handoff.sh）が行う。読む側の templates が
// 持つのは効力のある置き場（共通とレイヤー）だけで、ここは効力の無い下書きを扱う。
//
// 下書きの after は各計画の中の添字にする。親の計画の after は全体計画の続きの絶対番号なので、フィードバック計画は
// 全体計画のステップ数を引くだけで足りる（承認が順序を確かめている）。子のフローは全体計画の n 番目のステップの
// 子のうち、フローを持つ最初の 1 本をコピーする。固有名の一般化はエージェントが直すので、description と when に
// templates.TodoMark を付ける（残っていれば検査が warn で言う）。取り込み先や置き場に同名のものが在れば上書きせず断る。
//
// Python からの対応: tickets/sync/template_drafts。TO_COMMON・TO_PROJECT・TARGETS → ToCommon・ToProject・Targets、
// scan → Scan（DraftScan は Scanned）、draft_json → DraftJSON、lint_problems → LintProblems、make → Make
// （引数は Request、失敗は (nil, 理由)）、close_notice → CloseNotice。
package drafts
