// Package wiki は学びの蓄積の wiki（`docs/wiki/`）のパターンと採否の記録を読み、パターンの状態を決める。
//
// パターン（patterns/<名前>.md）は反映先の種類（target）と観測元の ID（sources。重複を除いた数が観測元の数）を、
// 採否の記録（impact/<提案>.md）は動機のパターンとそのときの観測元の数（patterns）と決まり方（status）を持つ。
// frontmatter が壊れたファイルは名前と理由を Wiki.Broken に積み、数えない。ファイルは読むだけで書かない。
// 読み先はワークツリー（LoadTree）か、チェックアウトせずに git のオブジェクト（LoadCommit）。
// シンボリックリンクと大きすぎるファイルは読まず、壊れたファイルとして名指しする。
//
// パターンの状態は、承認待ちの記録が 1 つでもあれば提案中、それ以外は decided_at が最も新しい記録で
// 決める（同じ日時なら提案の名前順で後のもの）。承認・却下なら決着、延期は今の観測元の数が記録の数より
// 多ければ候補、多くなければ決着。記録が無ければ候補。名前は大文字小文字を区別せず比べる。
//
// Python からの対応: wiki と wiki_load をまとめた。build → Build、rows → Rows、candidates → Candidates、
// pending → Pending、parse_pattern → ParsePattern、sources_threshold・notice_count → SourcesThreshold・
// NoticeCount、PATTERNS_DIR・IMPACT_DIR → PatternsDir・ImpactDir、TARGETS → Targets、
// wiki_load.load_tree・load_commit・commit_of → LoadTree・LoadCommit・CommitOf（期限は time.Time。
// ゼロ値なら git の既定の期限）。
package wiki
