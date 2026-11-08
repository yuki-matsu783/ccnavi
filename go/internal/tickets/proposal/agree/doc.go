// Package agree は、ユーザが提案に合意する（承認する）までの手続きを受け持つ。手順は次のとおり。承認の対象を組み（Gather。
// 候補と検査は candidates）、ユーザに見せ（screen・PreviewBody）、見せたものから変わっていないかを確かめ
// （ProposalsDigest・VerifyVerdict）、置き場へ動かす書き込みを並べ（PlanBatch）、承認の事実をモデルに伝える文を組む
// （ApprovedText）。
//
// 置き場は approval が、合意の手続きはここが受け持つ。import の向きは agree → approval の 1 本だけ。判定は承認済み
// チケットだけを読み、承認コマンドを通ったかどうかは材料にしない。置き場を手で動かす進め方もあるため。判定の側で要る
// 構造の検査は approval.blockingProblems にある。新規の承認は、提案の中身を変えずに動かすだけで、書き足すものは無い。
// 承認の JSON の形とダイジェストの値は、拡張（extensions/vscode/ccnavi-board の approvemodel.ts）との契約。
package agree
