// Package templates は作業・レビュー計画のテンプレート（1 つの親の全体計画・フィードバック計画と、各子のフローの
// 本文を 1 ファイルにまとめたもの）を読み、足し、検査し、使う形に組む。ファイルは書かない（下書きと取り込みは
// sync、提案への書き出しは scaffold）。置き場は共通（config/common/templates/）と各レイヤー
// （config/project/templates/）で、使うのは共通と 1 つのレイヤーの和。同名で全欄が同じならレイヤーの側を info で
// 破棄し、中身が違えば error でレイヤーの側だけを使わない。読み手はフローと同じ（リンク・ハードリンク・大きさ・
// 別名・UTF-8）。計画の検査は、合成した親の提案を ticket.Parse に通して承認の検査を当てる。順序は error、型 id と
// kind の食い違いは warn（定義は後から変わり、使うときの承認で改めて error になる）。--templates の JSON は拡張
// （ccnavi-board の templatesmodel.ts）との契約。
//
// Python からの対応: tickets/proposal の plan_templates と template_use をまとめた。parse_template → Parse、
// dump・to_yaml・dump_flow → Dump・ToYAML・DumpFlow、check → Check、graph → Graph、read_entry・read_layer・merge →
// ReadEntry・ReadLayer・Merge、common_writable・types_for・recheck → CommonWritable・TypesFor・Recheck、scan → Load
// （Scan）、adopted・find・problems_of・has_any → Scan.Adopted・Find・Problems・HasAny、matches → Matches、
// relative → Relative、digest → SHA、entry_json・as_json・check_file → EntryJSON・AsJSON・CheckFile、
// new_name_problem → NewNameProblem、hidden_chars・hidden_problem・escape_char・reveal → HiddenChars・
// HiddenProblem・EscapeChar・Reveal、FIELDS・SUFFIX・LAYER_DRAFT・TODO_MARK・JSON_VERSION・DROP_* → Fields・Suffix・
// LayerDraft・TodoMark・JSONVersion・Drop*、LAYER_COMMON・LAYER_SELF → config.LayerCommon・LayerSelf、
// Item.review の bool|None → ticket.Need。template_use の resolve・absolute・child_id・child_problem・flow_drafts →
// Resolve・Absolute・ChildID・ChildProblem・FlowDrafts（下書きの組は Draft）、FIRST_SEQ → FirstSeq。
package templates
