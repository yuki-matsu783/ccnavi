// Package suggest は `ccnavi --suggest` である。判定の記録（logs/decisions.jsonl と同じ置き場の decisions.*.
// jsonl）を数えて、ルールの候補を 2 種類起こす。1 つは、どのルールにも言及されず権限モードへ渡った呼び出しが、
// 同じ形で何度も来ているもの（ask のルールの下書き）。もう 1 つは、
// 同じルールが同じ呼び出しを繰り返し止めているもの（message の見直し）である。
//
// allow の候補は出さない。記録から通す側の候補を起こすと、「よく来るから通す」という理由で判定を緩める変更を機械が
// 勧めることになるからである。出すのは検証を通ったものだけである。
// 候補ごとに --lint と同じ読み（rulelint）でルールを確かめ、--test-expectations と同じ判定（diagnose.TryOne）で期
// 待を回す。ルールを足す候補は、共通レイヤーのコピーに 1 件足した一時ファイルで試し、実際のファイルには書かない。
// 置くのはユーザである（/ccnavi-config の手順）。ネットワークには出ない。
//
// Python からの対応は次のとおり。
//   - entry/suggest を移した。report → Report、Candidate → 私的な candidate。collect と私的な関数は、
//     同じ名前の私的な関数にした。
//   - log_files・read_records → audit.LogFiles・audit.ReadRecords、
//     repeat.threshold・normalize → tally.DenyThreshold・tally.Normalize、urlsplit の scheme と netloc → splitURL。
package suggest
