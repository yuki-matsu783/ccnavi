package flow

// フィードバック計画の承認は、レビューの状態を変えない。元: tests/ticket/test_feedback_plan_approval.py。
//
//  1. 全体計画の最後のフェーズがレビュー済みでなければ、フィードバック計画を初めて足す改版は拒む
//  2. レビュー済みなら通る。手で done/ に動かした子の確定（via: manual）の後でも通る
//  3. 承認の前後で、計画の差し替え以外（reviewed のバイト列・accepted.json・子の置き場・settled_by）は変わらない
//  4. まとめて承認するとき、初回のフィードバック計画の改版と、同じ親の全体計画の番号の子が同じ承認に入っていたら
//     両方拒む。無関係の親子は通り、フィードバック計画のフェーズの子との同時承認は許す
import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// requestedTree はフェーズ 1 のレビューを依頼して、親がコミットして push したところまでの木と結果ファイル。
func requestedTree(t *testing.T) (*Tree, ticketkit.Fixture) {
	t.Helper()
	tr := cachedTree(t, "feedback-requested", func(tr *Tree) {
		tr.Phase1Requested()
		tr.CommitParent("request")
		tr.Parent("push", "--quiet", "origin", ticketkit.ParentName)
	})
	return tr, tr.Fixture()
}

func (tr *Tree) proposeFeedbackPlan(feedback []samples.Step) {
	tr.StartParent()
	tr.Propose("i0001", samples.ParentText("i0001", samples.Plan("design"), samples.ParentOpts{Feedback: feedback}), "")
}

var singleFeedback = samples.Plan("implement-feedback")

func (tr *Tree) phaseDir() string { return filepath.Join(tr.Approved, "phases", ticketkit.ParentName) }

// reviewedMark はフェーズ 1 のレビュー済みのマーカー。無ければ nil。
func (tr *Tree) reviewedMark() map[string]any {
	path := filepath.Join(tr.phaseDir(), "1.reviewed")
	if !ticketkit.Exists(path) {
		return nil
	}
	return ticketkit.ReadJSON(tr.T, path)
}

func snapshot(t *testing.T, root string) map[string]string {
	t.Helper()
	out := map[string]string{}
	_ = filepath.WalkDir(root, func(p string, d os.DirEntry, err error) error {
		if err == nil && !d.IsDir() {
			rel, _ := filepath.Rel(root, p)
			out[filepath.ToSlash(rel)] = ticketkit.Read(t, p)
		}
		return nil
	})
	return out
}

func TestFeedbackPlanIsRefusedUntilTheLastReviewIsDone(t *testing.T) {
	t.Run("requested only", func(t *testing.T) {
		t.Parallel()
		tr, _ := requestedTree(t)
		tr.proposeFeedbackPlan(singleFeedback)
		ticketkit.Has(t, ticketkit.Fails(t, tr.Approve()).Stderr, "レビューが済んでから")
		if tr.reviewedMark() != nil {
			t.Error("レビュー済みになった")
		}
	})
	t.Run("unresolved threads keep the last review open", func(t *testing.T) {
		t.Parallel()
		tr, f := requestedTree(t)
		f.SetThreads(ticketkit.Thread{ID: "t0", Resolved: false, URL: "u/7#t0", Body: "直して"})
		ticketkit.Fails(t, tr.ConfirmFor(f, "1"))
		tr.proposeFeedbackPlan(singleFeedback)
		ticketkit.Has(t, ticketkit.Fails(t, tr.Approve()).Stderr, "レビューが済んでから")
		// 拒んだだけで、何も動かしていない。
		if tr.reviewedMark() != nil || ticketkit.Exists(filepath.Join(tr.phaseDir(), "accepted.json")) {
			t.Error("拒んだのにマーカーが動いた")
		}
	})
}

func TestReviewedPassesAndNothingButThePlanChanges(t *testing.T) {
	t.Parallel()
	tr, f := requestedTree(t)
	ticketkit.OK(t, tr.ConfirmFor(f, "1"))
	tr.CommitParent("reviewed")
	tr.proposeFeedbackPlan(singleFeedback)
	before := snapshot(t, tr.Approved)
	approved := ticketkit.OK(t, tr.Agree("y"))
	ticketkit.Lacks(t, approved.Stdout, "済ませた扱い")
	ticketkit.Lacks(t, approved.Stdout, "レビュー待ちの子を閉じた")
	after := snapshot(t, tr.Approved)
	var changed []string
	for k := range before {
		if before[k] != after[k] {
			changed = append(changed, k)
		}
	}
	for k := range after {
		if _, ok := before[k]; !ok {
			changed = append(changed, k)
		}
	}
	slices.Sort(changed)
	if !slices.Equal(slices.Compact(changed), []string{"doing/i0001.md"}) {
		t.Errorf("変わったファイル = %v", changed)
	}
	ticketkit.Has(t, after["doing/i0001.md"], "feedback:")
	ticketkit.Lacks(t, after["doing/i0001.md"], "feedback-plan")
}

// 承認は review/ の子を done/ へ動かさず、settled_by も書かない。
func TestAChildInReviewStaysWhereItIs(t *testing.T) {
	t.Parallel()
	tr, _ := requestedTree(t)
	review := filepath.Join(tr.ParentTree, "wip", "proposals", "review", "i0001-01-01.md")
	if !ticketkit.Exists(review) {
		t.Fatal("review/ に子が無い")
	}
	tr.proposeFeedbackPlan(singleFeedback)
	ticketkit.Fails(t, tr.Approve())
	if !ticketkit.Exists(review) || ticketkit.Exists(tr.ApprovedPath("done", "i0001-01-01")) {
		t.Error("子が動いた")
	}
	ticketkit.Lacks(t, ticketkit.Read(t, review), "settled_by")
}

func TestPassesAfterAManuallyMovedChildIsSettled(t *testing.T) {
	t.Parallel()
	tr, f := requestedTree(t)
	waiting := filepath.Join(tr.ParentTree, "wip", "proposals", "review", "i0001-01-01.md")
	done := tr.ApprovedPath("done", "i0001-01-01")
	if err := os.MkdirAll(filepath.Dir(done), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(waiting, done); err != nil {
		t.Fatal(err)
	}
	tr.CommitParent("move by hand")
	tr.Parent("push", "--quiet", "origin", ticketkit.ParentName)
	f.Update(func(d map[string]any) { d["threads"], d["reviews"] = []any{}, []any{} })
	ticketkit.OK(t, tr.ConfirmFor(f, "1"))
	if js(tr.reviewedMark(), "via") != "manual" {
		t.Errorf("reviewed = %v", tr.reviewedMark())
	}
	tr.CommitParent("settled")
	tr.proposeFeedbackPlan([]samples.Step{})
	ticketkit.OK(t, tr.Approve())
	if js(tr.reviewedMark(), "via") != "manual" {
		t.Errorf("承認の後の reviewed = %v", tr.reviewedMark())
	}
}

// reviewedWithPlanChild はレビュー済みで、フィードバック計画の改版と、全体計画の番号の子 i0001-01-02 を提案した木。
func reviewedWithPlanChild(t *testing.T) *Tree {
	t.Helper()
	tr, f := requestedTree(t)
	ticketkit.OK(t, tr.ConfirmFor(f, "1"))
	tr.CommitParent("reviewed")
	tr.proposeFeedbackPlan(singleFeedback)
	tr.Propose("i0001-01-02", child("i0001-01-02", 1, "wip/design/*"), "")
	return tr
}

func (tr *Tree) previewAgree(only ...string) map[string]any {
	tr.T.Helper()
	return ticketkit.ParseJSON(tr.T, tr.Ccnavi(append([]string{"--agree", "--preview", "--json"}, only...)...).Stdout)
}

func TestTheRevisionAndThePlanChildAreBothRefused(t *testing.T) {
	t.Parallel()
	tr := reviewedWithPlanChild(t)
	for _, only := range [][]string{nil, {"i0001", "i0001-01-02"}} {
		shown := tr.previewAgree(only...)
		if len(jl(shown, "batch")) != 0 {
			t.Errorf("%v: batch = %v", only, shown["batch"])
		}
		refused := map[string]string{}
		for _, r := range jl(shown, "rejected") {
			refused[js(r, "ticket")] = strings.Join(strs(jl(r, "problems")), "\n")
		}
		if len(refused) != 2 || refused["i0001"] == "" || refused["i0001-01-02"] == "" {
			t.Fatalf("%v: rejected = %v", only, refused)
		}
		for _, text := range refused {
			for _, w := range []string{"全体計画に足す子を先に承認し", "再レビュー（レビュー済み）を済ませてから", "フィードバック計画の改版を承認する"} {
				ticketkit.Has(t, text, w)
			}
		}
	}
	// 承認は何も置かない。
	tr.Approve()
	if !ticketkit.Exists(tr.TodoPath("i0001")) || !ticketkit.Exists(tr.TodoPath("i0001-01-02")) || ticketkit.Exists(tr.ApprovedPath("doing", "i0001-01-02")) {
		t.Error("拒んだはずの提案が動いた")
	}
	ticketkit.Lacks(t, ticketkit.Read(t, tr.ApprovedPath("doing", "i0001")), "feedback:")
}

func TestThePlanChildFirstThenTheRevisionAfterTheReview(t *testing.T) {
	t.Parallel()
	tr := reviewedWithPlanChild(t)
	held := filepath.Join(tr.Root, "revision.md")
	if err := os.Rename(tr.TodoPath("i0001"), held); err != nil {
		t.Fatal(err)
	}
	ticketkit.OK(t, tr.Approve())
	if !ticketkit.Exists(tr.ApprovedPath("doing", "i0001-01-02")) {
		t.Error("子が承認されない")
	}
	// 子を足すとフェーズは開き直る。作業と再レビューを済ませるまで、改版は通らない。
	if err := os.Rename(held, tr.TodoPath("i0001")); err != nil {
		t.Fatal(err)
	}
	ticketkit.Has(t, ticketkit.Fails(t, tr.Approve()).Stderr, "レビューが済んでから")
}

func TestAFeedbackPhaseChildMayBeApprovedWithTheRevision(t *testing.T) {
	t.Parallel()
	tr, f := requestedTree(t)
	ticketkit.OK(t, tr.ConfirmFor(f, "1"))
	tr.CommitParent("reviewed")
	tr.proposeFeedbackPlan(singleFeedback)
	tr.Propose("i0001-02-02", child("i0001-02-02", 2, "src/*"), "")
	shown := tr.previewAgree()
	if len(jl(shown, "rejected")) != 0 {
		t.Errorf("rejected = %v", shown["rejected"])
	}
	var batch []string
	for _, b := range jl(shown, "batch") {
		batch = append(batch, js(b, "ticket"))
	}
	slices.Sort(batch)
	if !slices.Equal(batch, []string{"i0001", "i0001-02-02"}) {
		t.Errorf("batch = %v", batch)
	}
	ticketkit.OK(t, tr.Approve())
	if !ticketkit.Exists(tr.ApprovedPath("doing", "i0001-02-02")) {
		t.Error("フィードバックのフェーズの子が承認されない")
	}
}

func TestAnUnrelatedFamilyIsApprovedByThePartialApprovalRules(t *testing.T) {
	t.Parallel()
	tr := reviewedWithPlanChild(t)
	other := tr.Worktree("i0002", "main")
	tr.Propose("i0002", parentOf("i0002", "design"), other)
	var refused []string
	for _, r := range jl(tr.previewAgree(), "rejected") {
		refused = append(refused, js(r, "ticket"))
	}
	slices.Sort(refused)
	if !slices.Equal(refused, []string{"i0001", "i0001-01-02"}) {
		t.Errorf("rejected = %v", refused)
	}
}
