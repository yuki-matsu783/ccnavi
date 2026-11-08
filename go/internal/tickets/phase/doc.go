// Package phase は、親の計画のフェーズを組み、フェーズの終わりと HITL ポイント（ユーザのレビューを待つところ）を決める。
// フェーズは、承認済みチケットの置き場とマーカーから組む。
//
// 扱うものは次のとおり。レビューが済むまでサブエージェントの起動とシェルを止めるゲート（Phase.GateClosed）、止めたときの
// 文（HoldReason）、終わったフェーズの 1 度きりの知らせ（Announce）、子の承認の順序（OrderProblems）、親の局面（Stage）、
// 手で done/ に動かした子の決着（AssessManual）。止めるかどうかの条件は Phase.GateClosed の 1 か所だけに置く。hook・
// ボード・Stop の促しは、それを読む。起動とシェルには書き込み先が無いので、止める親は cwd のワークツリーで引く（ParentAt）。
// 子の範囲の当て方は scope が決める。
package phase
