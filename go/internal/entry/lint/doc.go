// Package lint は、設定とルールの検証（`--lint`）と、取り込み後の検査（`sync check`）を持つ。判定は行わず、
// 防御を無効化しうる記述だけを報告する。判定の経路は、判定のついでに気づいたことしか言わない。そのため、
// ルールを書き換えた直後、CI、入れたばかりのプロジェクトで不備を見つける経路はここになる。
//
// 深刻度は 1 つの原則で決める。error は、ガードが働かない、あるいは働きすぎて全部を止める記述である（CI を落とすの
// はここだけ）。warn は、判定は動くが、書いたユーザが意図した防御が働いていない記述である。info は、
// 書いたとおりに働いているが、書いたユーザが知りたいはずのことである（終了コードには影響しない）。
//
// Python からの対応は次のとおり。
//   - entry の lint・lint_branch・lint_direct・lint_layers・lint_places・lint_project・lint_templates・lint_texts・
//     lint_ticket・lint_wiki をまとめた。
//   - report → Report（引数は Options。version.COMPAT は段が同じ version を読めないので、Options.Compat で受ける）、
//     family_check → FamilyCheck（repo の None は空）、LINT_VERSION → jsonVersion。
//   - ほかの関数（stop_review_problems・flow_problems・layer_drift・tree_has_tickets・compat_fix など）は私的にした。
//     呼び手は lint の中だけである。
package lint
