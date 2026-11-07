// Package form は提案の形式の検査（最小版）を持つ。雛形の空欄の目印が残っていないかと、親の新規の提案に必須の
// 見出しが欠けていないかの 2 つだけを見る。ticket new の雛形と承認の検査の両方が読む定数と検査を置く。
// 文字列を調べるだけで、ファイルも git も読まない。
//
// Python からの対応: proposal_form をそのまま移した。PARENT_HEADINGS・RATIONALE_TODO・HEADING_TODO・PLACEHOLDERS →
// ParentHeadings・RationaleTodo・HeadingTodo・placeholders、headings_in → headings、form_problems → Problems。
package form
