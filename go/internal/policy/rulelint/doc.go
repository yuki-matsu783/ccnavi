// Package rulelint は、ルールファイルの中身を検査する。組み上がらないルール、何も止めない match、届かない
// message、広すぎる allow などを名指しする。`--lint`・保護対象の下書きの取り込み・提案（suggest）が呼ぶ。
// 判定と同じ読み（rules.Load）で読む。別の読み方をすると、検査は通ったのに実運用で失敗するためである。
//
// Python からの対応: policy.rule_lint。file_problems → FileProblems（home・layer・project は Options）。私的な関数は
// 名前の頭の _ を外した同じ名前（_rule_problems → ruleProblems、_stop_problems → stopProblems、_except_problems →
// exceptProblems、_bound_to_command → boundToCommand、_constant → constant、_catch_all_deny_problems →
// catchAllDenyProblems、_when_problems → whenProblems、_every_problems → everyProblems、_broad → broad、
// _alternatives → alternatives、_inert → inert）。
package rulelint
