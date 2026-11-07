// Package suggest は `ccnavi --suggest`。判定の記録（logs/decisions.jsonl と同じ置き場の decisions.*.jsonl）を数えて、
// ルールの候補を 2 種類起こす。どのルールも言及せず権限モードへ渡った呼び出しが同じ形で何度も来ているもの（ask のルール
// の下書き）と、同じルールが同じ呼び出しを繰り返し止めているもの（message の見直し）である。
//
// allow の候補は出さない。記録から通す側の候補を起こすと「よく来るから通す」になり、判定を緩める変更を機械が勧めることに
// なる。出すのは検証を通ったものだけで、候補ごとに --lint と同じ読み（rulelint）でルールを確かめ、--test-expectations と
// 同じ判定（diagnose.TryOne）で期待を回す。ルールを足す候補は、共通レイヤーのコピーに 1 件足した一時ファイルで試し、
// 実際のファイルには書かない。置くのはユーザ（/ccnavi-config の手順）。ネットワークには出ない。
//
// Python からの対応: entry/suggest。report → Report、Candidate → 私的な candidate、collect と私的な関数は同じ名前の私的な関数にした。
// log_files・read_records → audit.LogFiles・audit.ReadRecords、repeat.threshold・normalize → tally.DenyThreshold・
// tally.Normalize、urlsplit の scheme と netloc → splitURL。
package suggest
