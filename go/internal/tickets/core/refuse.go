package core

import (
	"strings"
)

// code は通らなかった根拠の種別（付録 A の CORE_*）。ターミナル・ボード・Chrome・試験が文の言い回しでなく同じ材料で
// 分けられるよう、文面の頭に `[<コード>]` で載せる。
type code string

const (
	// 承認（--agree --yes）。
	codeApproveNoDigest      code = "CORE_APPROVE_NO_DIGEST"      // --digest が無い
	codeApproveListChanged   code = "CORE_APPROVE_LIST_CHANGED"   // 見せた識別子と今の一覧が違う
	codeApproveDigestChanged code = "CORE_APPROVE_DIGEST_CHANGED" // 識別子は同じで、見せた中身が今と違う
	codeApproveNothing       code = "CORE_APPROVE_NOTHING"        // 承認するものが無い
	codeApproveStopped       code = "CORE_APPROVE_STOPPED"        // 書く途中で止まった（置いたものは残る）

	// レビュー済み（review confirm）。
	codeConfirmNoParent        code = "CORE_CONFIRM_NO_PARENT"        // 親の承認済みチケットが作業中に無い
	codeConfirmNoPhase         code = "CORE_CONFIRM_NO_PHASE"         // 親にそのフェーズの子が無い
	codeConfirmAlreadyReviewed code = "CORE_CONFIRM_ALREADY_REVIEWED" // そのフェーズはもうレビュー済み
	codeConfirmNotRequested    code = "CORE_CONFIRM_NOT_REQUESTED"    // 依頼の記録が無い
	codeConfirmMoved           code = "CORE_CONFIRM_MOVED"            // 依頼の後にユーザが見るものが動いた
	codeConfirmResultMismatch  code = "CORE_CONFIRM_RESULT_MISMATCH"  // 結果が依頼したマージリクエストと合わない
	codeConfirmReviewOpen      code = "CORE_CONFIRM_REVIEW_OPEN"      // 変更要求か未解決のスレッドが残る

	// 残った指摘の行き先（Chrome の decide）。
	codeDecideNoDigest code = "CORE_DECIDE_NO_DIGEST" // 見せた指摘のダイジェストが無い・読めない
	codeDecideMismatch code = "CORE_DECIDE_MISMATCH"  // 見せた指摘と今の指摘が違う
	codeDecideChoices  code = "CORE_DECIDE_CHOICES"   // 選択（か受け入れ済みのキー）の形が見せた指摘と合わない
	codeDecideThreadID code = "CORE_DECIDE_THREAD_ID" // 返信と解決をするスレッドの id が無い・API の経路に使えない
	codeDecideFollowup code = "CORE_DECIDE_FOLLOWUP"  // 続きの子を起こせない
	codeDecideStopped  code = "CORE_DECIDE_STOPPED"   // 書くものを並べる途中で止まった（何も積まない）
)

const linePrefix = "ccnavi: "

// tagged は文面の 1 行にコードを載せる。`ccnavi: ` で始まる行はその後ろに、ほかは頭に置く。
func tagged(c code, line string) string {
	if rest, ok := strings.CutPrefix(line, linePrefix); ok {
		return linePrefix + "[" + string(c) + "] " + rest
	}
	return "[" + string(c) + "] " + line
}

// refused は通らなかった答えの文面。最初の行にだけコードを載せる（続きの行は最初の行の補足）。
func refused(c code, problems []string) []string {
	lines := append([]string{}, problems...)
	if len(lines) == 0 {
		lines = []string{linePrefix + string(c)}
	}
	lines[0] = tagged(c, lines[0])
	return lines
}
