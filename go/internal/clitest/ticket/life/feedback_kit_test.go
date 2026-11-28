package life

import (
	"path/filepath"
	"testing"

	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// フィードバックのフェーズの計画を承認する場面の準備。フィードバック計画待ちの Stop の促しと、
// 改版の雛形（ticket revise）が使う。

// feedbackStep はフィードバック計画（implement-feedback の 1 ステップ）。
var feedbackStep = samples.Plan("implement-feedback")

// buildRequested は、フェーズ 1 の子をクローズして合流し、レビューを依頼して push したところまで組む。
func buildRequested(tr *ticketkit.Tree) {
	t := tr.T
	t.Helper()
	tr.PhaseFamily(samples.Plan("design"))
	tr.Propose(childName, samples.ChildText(childName, "i0001", 1, []string{"wip/design/*"}), "")
	tr.CommitParent("tickets")
	ticketkit.OK(t, tr.Approve())
	tr.RunChild(childName, ticketkit.File{Path: "wip/design/plan.md", Text: "d\n"})
	ticketkit.OK(t, tr.Ccnavi("ticket", "finish", childName))
	tr.CommitParent("close 01")
	tr.Merge(childName)
	f := tr.Origin()
	ticketkit.OK(t, tr.Request(f))
	tr.CommitParent("request")
	tr.Parent("push", "--quiet", "origin", "i0001")
}

// requestedTree はレビューを依頼したところまでの木（Cached）。
func requestedTree(t *testing.T) (*ticketkit.Tree, ticketkit.Fixture) {
	t.Helper()
	tr := ticketkit.Cached(t, "feedback-requested", ticketkit.Options{Phases: samples.Phases}, buildRequested)
	return tr, tr.Fixture()
}

// waitingTree は全体計画のレビューが済み、フィードバック計画が決まっていない局面までの木（Cached）。
func waitingTree(t *testing.T) *ticketkit.Tree {
	t.Helper()
	return ticketkit.Cached(t, "feedback-waiting", ticketkit.Options{Phases: samples.Phases}, func(tr *ticketkit.Tree) {
		buildRequested(tr)
		ticketkit.OK(tr.T, tr.Confirm(tr.Fixture()))
		tr.CommitParent("reviewed")
	})
}

// proposeFeedback は親を着手して、親の提案（design の計画とフィードバック計画 feedback）を置く。nil なら feedbackStep。
func proposeFeedback(tr *ticketkit.Tree, feedback []samples.Step) {
	tr.T.Helper()
	tr.StartParent()
	if feedback == nil {
		feedback = feedbackStep
	}
	tr.Propose("i0001", samples.ParentText("i0001", samples.Plan("design"), samples.ParentOpts{Feedback: feedback, Copy: tr.Phases}), "")
}

func todoDir(tr *ticketkit.Tree) string { return filepath.Dir(tr.TodoPath("i0001")) }
