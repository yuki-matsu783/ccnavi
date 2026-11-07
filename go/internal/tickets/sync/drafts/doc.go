// Package drafts は、作業・レビュー計画のテンプレートの下書きを扱う。下書きを、完了した親から作る（Make）。全ツリーの
// 下書きを読んで検査する（Scan）。親が閉じるときに、テンプレート化を案内する（CloseNotice）。下書きの置き場は、
// ほかの保護対象と同じ wip/handoff/<取り込み先の相対パス> である。取り込みは handoff（ccnavi-user-handoff.sh）が行う。
// 読む側の templates が持つのは効力のある置き場（共通とレイヤー）だけである。ここは効力の無い下書きを扱う。
//
// 下書きの after は、各計画の中の添字にする。親の計画の after は全体計画の続きの絶対番号なので、フィードバック計画は
// 全体計画のステップ数を引くだけで足りる。承認が順序を確かめているからである。子のフローは、全体計画の n 番目の
// ステップの子のうち、フローを持つ最初の 1 本をコピーする。固有名の一般化はエージェントが直す。そのため、description と
// when に templates.TodoMark を付ける。残っていれば検査が warn で言う。取り込み先や置き場に同名のものが在れば、上書きせず断る。
//
// Python からの対応: tickets/sync/template_drafts。TO_COMMON・TO_PROJECT・TARGETS → ToCommon・ToProject・Targets、
// scan → Scan（DraftScan は Scanned）、draft_json → DraftJSON、lint_problems → LintProblems、make → Make
// （引数は Request、失敗は (nil, 理由)）、close_notice → CloseNotice。
package drafts
