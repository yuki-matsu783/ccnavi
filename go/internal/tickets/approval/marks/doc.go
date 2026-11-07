// Package marks は承認済みの領域の `phases/<親>/` に置くマーカーと記録を読み書きする。フェーズのマーカー
// （`<N>.<種類>`。レビューが済むまで止める判定が見る）、親ごとのマーカー（`ready`・`close-early`・`closed` の
// `.json`）、受け入れ台帳（`accepted.json`）、子ごとの記録（`<子>.risk.json`・`<子>.judge.json`）と、承認済み
// チケットの書き直し。ファイル名と JSON の形（キーの順・indent・ensure_ascii=False・機械の改行）は sh と拡張も
// 読むので変えない。
//
// Python からの対応: tickets/approval/marks を移した。MARK_* → Kind の定数、MARKS → Kinds、PHASES_DIR →
// place.PhasesDir、mark_path・read_mark・write_mark → MarkPath・ReadMark・WriteMark、clear_marks → Clear（announce は
// 消せた種類から見せる行を作る関数）、PARENT_MARK_* → Parent の定数、parent_mark_path・read_parent_mark・
// write_parent_mark → ParentPath・ReadParent・WriteParent、CHILD_RECORD_* → Record の定数、child_record_path・
// read_child_record・write_child_record → RecordPath・ReadRecord・WriteRecord、read_ledger・add_ledger →
// ReadLedger・AddLedger、LEDGER_NAME → Ledger、marks → Marks、write_ticket → WriteTicket。now は fsio.Stamp を直に
// 使う。呼び手の無い LEDGER_BY は移していない。
package marks
