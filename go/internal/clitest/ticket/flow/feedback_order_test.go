package flow

// フィードバック計画の順序。フィードバック計画も全体計画と同じく、ステップの after で先行関係が決まる。
// ターミナルの承認で、並行と合流を通す。元: tests/ticket/test_feedback_order.py。

import (
	"testing"

	"ccnavi/internal/harness/samples"
)

// fbParallel は並行する 2 ステップ（2・3）を最後のステップ（4）が受けるフィードバック計画。
var fbParallel = []samples.Step{
	{Type: "implement-feedback"},
	{Type: "implement-feedback", After: []int{}},
	{Type: "implement-feedback", After: []int{2, 3}},
}

// planReviewed は全体計画（design 1 ステップ）を閉じてレビューを済ませ、親を着手した木。
func planReviewed(t *testing.T) *Tree {
	t.Helper()
	return cachedTree(t, "plan-reviewed", func(tr *Tree) {
		OK(tr.T, tr.ConfirmFor(tr.Phase1Requested(), "1"))
		tr.StartParent()
	})
}

func (tr *Tree) proposeFeedback(plan []string, fb []samples.Step) {
	tr.Propose("i0001", samples.ParentText("i0001", samples.Plan(plan...), samples.ParentOpts{Feedback: fb}), "")
}

func (tr *Tree) proposeFeedbackChildren() {
	for _, n := range []string{"2", "3", "4"} {
		name := "i0001-0" + n + "-0" + n
		tr.Propose(name, samples.ChildText(name, ParentName, int(n[0]-'0'), []string{"src/*"}), "")
	}
}

func isApproved(tr *Tree, name string) bool { return Exists(tr.ApprovedPath("doing", name)) }

func TestAFeedbackPlanWithoutAJoinIsRefused(t *testing.T) {
	t.Parallel()
	tr := planReviewed(t)
	tr.proposeFeedback([]string{"design"}, []samples.Step{{Type: "implement-feedback"}, {Type: "implement-feedback", After: []int{}}})
	tr.CommitParent("feedback")
	refused := Fails(t, tr.Approve())
	Has(t, refused.Stderr, "`feedback` の最後のステップ")
	Has(t, refused.Stderr, "最後に 1 ステップで受ける")
}

func TestParallelFeedbackChildrenStartTogetherAndTheJoinWaits(t *testing.T) {
	t.Parallel()
	tr := planReviewed(t)
	tr.proposeFeedback([]string{"design"}, fbParallel)
	tr.CommitParent("feedback")
	OK(t, tr.Approve())
	tr.proposeFeedbackChildren()
	tr.CommitParent("approval-request feedback children")
	r := tr.Approve()
	for _, n := range []string{"i0001-02-02", "i0001-03-03"} {
		if !isApproved(tr, n) {
			t.Errorf("%s が承認されない: %s%s", n, r.Stdout, r.Stderr)
		}
	}
	if isApproved(tr, "i0001-04-04") {
		t.Error("合流のステップの子が先行より先に承認された")
	}
	Has(t, r.Stderr, "閉じるまで承認しない")
	explained := tr.Ccnavi("--explain").Stdout
	Has(t, explained, "フェーズ 3（実装フィードバック対応）")
	Has(t, explained, "先行 1")
}

// フィードバック計画を立てる改版と子を一緒に出しても、合流のステップの子は先行が閉じるまで承認されない。
func TestTheJoinChildWaitsEvenWhenApprovedWithTheFeedbackPlan(t *testing.T) {
	t.Parallel()
	tr := planReviewed(t)
	tr.proposeFeedback([]string{"design"}, fbParallel)
	tr.proposeFeedbackChildren()
	tr.CommitParent("feedback and children")
	r := tr.Approve()
	if !isApproved(tr, "i0001-02-02") || !isApproved(tr, "i0001-03-03") || isApproved(tr, "i0001-04-04") {
		t.Errorf("承認の結果が違う: 02=%v 03=%v 04=%v\n%s%s", isApproved(tr, "i0001-02-02"), isApproved(tr, "i0001-03-03"), isApproved(tr, "i0001-04-04"), r.Stdout, r.Stderr)
	}
	Has(t, r.Stderr, "閉じるまで承認しない")
}

func TestAnApprovedFeedbackPlanCannotChangeItsAfter(t *testing.T) {
	t.Parallel()
	tr := planReviewed(t)
	tr.proposeFeedback([]string{"design"}, fbParallel)
	tr.CommitParent("feedback")
	OK(t, tr.Approve())
	tr.proposeFeedback([]string{"design"}, []samples.Step{{Type: "implement-feedback"}, {Type: "implement-feedback", After: []int{2}}, {Type: "implement-feedback", After: []int{3}}})
	tr.CommitParent("change feedback")
	Has(t, Fails(t, tr.Approve()).Stderr, "1 回だけ")
}

func TestThePlanCannotChangeTogetherWithTheFirstFeedbackPlan(t *testing.T) {
	t.Parallel()
	tr := planReviewed(t)
	tr.Propose("i0001", samples.ParentText("i0001", []samples.Step{{Type: "design"}, {Type: "design", After: []int{1}}}, samples.ParentOpts{Feedback: samples.Plan("implement-feedback")}), "")
	tr.CommitParent("plan and feedback")
	Has(t, Fails(t, tr.Approve()).Stderr, "全体計画を変えられない")
}

func TestTheScreenSaysWhichFeedbackItemsStartTogether(t *testing.T) {
	t.Parallel()
	tr := planReviewed(t)
	tr.proposeFeedback([]string{"design"}, fbParallel)
	tr.CommitParent("feedback")
	r := OK(t, tr.Approve())
	Has(t, r.Stdout, "すぐ始まる（フィードバック計画）: 2, 3")
	Has(t, r.Stdout, "フィードバック計画を改版した")
}
