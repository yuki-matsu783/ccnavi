// Package agree は、ユーザが提案に合意する（承認する）までの手続きを持つ。手順は次のとおり。承認の対象を組み（Gather。
// 候補と検査は candidates）、ユーザに見せ（Screen・PreviewBody）、見せたものから変わっていないかを確かめ
// （ProposalsDigest・VerifyVerdict）、置き場へ動かす書き込みを並べ（PlanBatch）、承認の事実をモデルに伝える文を組む
// （ApprovedText）。
//
// 置き場は approval が持ち、合意の手続きはここが持つ。向きは agree → approval の 1 本だけである。判定は承認済みチケットだけを
// 読み、承認コマンドを通ったかどうかを材料にしない。置き場を手で動かす進め方もあるからである。判定の側で要る構造の検査は、
// approval.BlockingProblems にある。新規の承認は、提案の中身を変えずに動かすだけで、書き足すものは無い。
// 承認の JSON の形とダイジェストの値は、拡張（extensions/vscode/ccnavi-board の approvemodel.ts）との契約である。
//
// Python からの対応: tickets/proposal の agree・agree_digest・agree_screen をまとめた。agree: AGREE_VERSION →
// Version、Gathered（text・identifiers → Text・Identifiers）、gather → Gather、waiting → Waiting、preview_body →
// PreviewBody、VERIFY_* → VerifyReason の定数、Verdict・verify_verdict → Verdict・VerifyVerdict（held の組 → *Held）、
// Applied・Planned・plan_batch → Applied・Planned・PlanBatch（stopped の組 → *Stop。home_dir の NoParentWorktree は
// error で返す）。agree_digest: proposals_digest → ProposalsDigest、revise_copy・revised_front は私的。
// agree_screen: screen → Screen、approved_text → ApprovedText（worktrees の dict → ChildWorktree）。
package agree
