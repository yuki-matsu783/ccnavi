// Package lint は設定とルールの検証（`--lint`）と、取り込みの後の検査（`sync check`）を持つ。判定を行わずに、
// 防御を無効化しうる記述だけを報告する。判定の経路は判定のついでに気づいたことしか言わないので、ルールを書き換えた
// 直後・CI・入れたばかりのプロジェクトで不備を見つける経路はここになる。
//
// 深刻度は 1 つの原則で決める。error はガードが働かない、あるいは働きすぎて全部を止める記述（CI を落とすのはここだけ）。
// warn は判定は動くが、書いたユーザが意図した防御が働いていない記述。info は書いたとおりに働いているが、書いたユーザが
// 知りたいはずのこと（終了コードに影響しない）。
//
// Python からの対応: entry の lint・lint_branch・lint_direct・lint_layers・lint_places・lint_project・
// lint_templates・lint_texts・lint_ticket・lint_wiki をまとめた。report → Report（引数は Options。version.COMPAT は
// 段が同じ version を読めないので Options.Compat で受ける）、family_check → FamilyCheck（repo の None は空）、
// LINT_VERSION → JSONVersion。ほかの関数（stop_review_problems・flow_problems・layer_drift・tree_has_tickets・
// compat_fix など）は私的にした（呼び手は lint の中だけ）。
package lint
