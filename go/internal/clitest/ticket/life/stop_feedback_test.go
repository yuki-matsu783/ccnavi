package life

import (
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// フィードバック計画待ちのまま終えようとしたときの促し（NUDGE_TICKET_FEEDBACK）。全体計画のレビューが済んだ親は、閉じる前に
// フィードバック計画が要る。局面が「フィードバック計画待ち」の親のツリーで終えようとしたとき、1 回だけ止めて次の一手を返す。

func askedFeedback(t *testing.T, r run.Result) string {
	t.Helper()
	reason := asked(t, r, codeFeedback)
	ticketkit.Has(t, reason, "[ccnavi] "+codeFeedback+" (parent: i0001)")
	return reason
}

func TestTheFeedbackNudgeAsksOnceAndNamesTheTodoFolder(t *testing.T) {
	t.Parallel()
	tr := waitingTree(t)
	reason := askedFeedback(t, stop(tr, tr.ParentTree))
	ticketkit.Has(t, reason, todoDir(tr))
	ticketkit.Has(t, reason, "feedback: []")
	// 改版の雛形は手で写さず、コマンドが作る（スクリプトだけが書く欄の始末も済ませる）。
	ticketkit.Has(t, reason, base.ScriptCommand(tr.Root, "ccnavi-launcher.sh")+" ticket revise i0001 --feedback")
	ticketkit.Has(t, reason, "approval-request i0001 --check")
	ticketkit.Has(t, reason, "`"+base.ScriptCommand(tr.Root, "ccnavi-ticket.sh")+" approval-request i0001`")
	// ユーザが止めたときの扱いと、同じ状態では 1 回だけであること。
	ticketkit.Has(t, reason, "止めて")
	ticketkit.Has(t, reason, "同じ状態では 1 回だけ")
	notNudged(t, stop(tr, tr.ParentTree), codeFeedback)
}

func TestTheFeedbackNudgeIsNotGivenInAChainASubagentOrTheRootAndDoesNotUseItUp(t *testing.T) {
	t.Parallel()
	tr := waitingTree(t)
	notNudged(t, stop(tr, tr.ParentTree, stopOpts{Active: true}), codeFeedback)
	notNudged(t, stop(tr, tr.ParentTree, stopOpts{AgentID: "a1"}), codeFeedback)
	notNudged(t, stop(tr, tr.Root), codeFeedback)
	askedFeedback(t, stop(tr, tr.ParentTree))
}

func TestAFeedbackNudgeIsHeldByARunningSubagent(t *testing.T) {
	t.Parallel()
	tr := waitingTree(t)
	holdsWhileASubagentRuns(tr, tr.ParentTree, func() run.Result { return stop(tr, tr.ParentTree) }, codeFeedback)
}

func TestAnApprovedFeedbackPlanIsNotAsked(t *testing.T) {
	t.Parallel()
	tr := waitingTree(t)
	proposeFeedback(tr, []samples.Step{})
	ticketkit.OK(t, tr.Approve())
	notNudged(t, stop(tr, tr.ParentTree), codeFeedback)
}

func TestABlockedParentIsNotAskedForFeedback(t *testing.T) {
	t.Parallel()
	tr := waitingTree(t)
	path := tr.ApprovedPath("doing", "i0001")
	// workflow: の欄を持つ承認済みチケットは、判定が信頼しない（blocked）。
	tr.Replace(path, "version: 1\n", "version: 1\nworkflow: x\n")
	notNudged(t, stop(tr, tr.ParentTree), codeFeedback)
}

// 改版を提案済みなら、提案済みの文面になる。未提案から提案済みに変わったら、もう 1 回だけ促す。
func TestAProposedRevisionChangesTheTextAndAsksOnceMore(t *testing.T) {
	t.Parallel()
	tr := waitingTree(t)
	askedFeedback(t, stop(tr, tr.ParentTree))
	notNudged(t, stop(tr, tr.ParentTree), codeFeedback)
	// 改版を書く（feedback: [] を持つ親と同じ識別子の提案）。
	proposeFeedback(tr, []samples.Step{})
	reason := askedFeedback(t, stop(tr, tr.ParentTree))
	ticketSh := base.ScriptCommand(tr.Root, "ccnavi-ticket.sh")
	ticketkit.Has(t, reason, "フィードバック計画の改版は "+tr.TodoPath("i0001")+" にあります")
	ticketkit.Has(t, reason, "`"+ticketSh+" approval-request i0001`")
	ticketkit.Has(t, reason, "`"+ticketSh+" status i0001`")
	ticketkit.Lacks(t, reason, "started_at・completed_at")
	notNudged(t, stop(tr, tr.ParentTree), codeFeedback)
}

func TestAProposalWithoutFeedbackIsNotAProposedRevision(t *testing.T) {
	t.Parallel()
	tr := waitingTree(t)
	tr.StartParent()
	tr.Propose("i0001", samples.ParentText("i0001", samples.Plan("design"), samples.ParentOpts{Copy: tr.Phases}), "")
	reason := askedFeedback(t, stop(tr, tr.ParentTree))
	ticketkit.Has(t, reason, "全体計画のレビューが済み")
	ticketkit.Lacks(t, reason, "の改版は")
}
