// Package audit は判定の記録（`logs/decisions.jsonl`）。1 回の起動につき 1 行を追記する。
//
// 通した呼び出しも書く。記録が意味を持つのは「記録が無い呼び出し」との対比で、拒否だけを残すと、
// 動かなくなったガードと言うことが無かったガードが同じ見た目になる。コマンドの全文が入る欄は
// redact で伏せてから書く。伏せるのは記録だけで、判定は伏せる前の文字列で済んでいる。
//
// Python からの対応: Record → Record、Log → Log（write → Write は error を返す）、ALLOW などの判定 →
// Decision の定数、REASON_* → Reason の定数、_redacted → Redacted（上の段が見せる文にも使う）。
package audit
