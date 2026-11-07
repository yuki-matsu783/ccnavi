// Package audit は判定の記録（`logs/decisions.jsonl`）。1 回の起動につき 1 行を追記する。
//
// 通した呼び出しも書く。記録が意味を持つのは、記録が無い呼び出しとの対比があるから。
// 拒否だけを残すと、動かなくなったガードと、言うことが無かったガードが同じ見た目になってしまう。
// redact で伏せてから書く。伏せるのは記録だけで、判定は伏せる前の文字列で済んでいる。
//
// Python からの対応: Record → Record、Log → Log（write → Write は error を返す）、ALLOW などの判定 →
// Decision の定数、REASON_* → Reason の定数、_redacted → redact.Redact（Go の伏せ字は失敗しないので、REDACT_FAILED に置き換える経路は持たない）。
// SUBJECT_LIMIT → recordedSubjectLimit（texts.ShownSubjectLimit とは別の上限）。tickets.learning.notes.read_log_records
// → ReadRecords、entry/suggest の log_files・read_records → LogFiles・ReadRecords。
package audit
