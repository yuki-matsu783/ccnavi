package life

import (
	"path/filepath"
	"testing"

	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// 再開（done/ から doing/ へ手で戻す）で残った reviewed のマーカーの警告。運用の基本は、閉じたチケットを戻さず新しいチケットを作り直すこと。
// 再開されると、そのフェーズの reviewed が残る。機構はマーカーを消さず、--lint と ticket status が warn で言う。
//  1. 作業中の子があり、レビューが要るフェーズに reviewed が残っていれば、lint と status が言う
//  2. 言わないもの: reviewed が無い、子が閉じている（done/）、レビューが要らないフェーズ
//  3. 警告は判定と start を止めない
//  4. 定義が review: false でも、再開された子が human_review を求めれば言う（閉じ直すと要る）
//  5. 延期したフェーズの子が作業中で、そのフェーズが一度終わった印（skipped）が残っていれば言う。引き受け手は辿らず、マーカーの置き場と
//     消し方（番号は <番号>）を言う

const markerWords = "reviewed マーカーが残っています"

const (
	deferredEnded = "延期したフェーズ 1（設計） は一度終わっています"
)

// childWithMarker は親と子 1 本を承認して着手し、必要ならフェーズ 1 に reviewed のマーカーを置く。
func childWithMarker(t *testing.T, plan string, review samples.Flag, marker, start bool) *ticketkit.Tree {
	t.Helper()
	tr := ticketkit.New(t, ticketkit.Options{Phases: samples.Phases})
	tr.PhaseFamily(samples.Plan(plan))
	tr.Propose(childName, samples.ChildTextReview(childName, "i0001", 1, []string{"wip/*"}, review), "")
	tr.CommitParent("tickets")
	ticketkit.OK(t, tr.Approve())
	tr.StartParent()
	tr.Worktree(childName, "i0001")
	if start {
		ticketkit.OK(t, tr.Ccnavi("ticket", "start", childName))
	}
	if marker {
		ticketkit.Write(t, filepath.Join(tr.Approved, "phases", "i0001", "1.reviewed"), `{"at": "2026-01-01T00:00:00+0000"}`+"\n")
	}
	tr.CommitParent("marker")
	return tr
}

// deferredChild はフェーズ 1（設計、延期）の子が作業中の木。skipped なら 1 が一度終わった印を置く。
func deferredChild(t *testing.T, skipped bool) *ticketkit.Tree {
	t.Helper()
	tr := ticketkit.New(t, ticketkit.Options{Phases: samples.Phases})
	tr.PhaseFamily([]samples.Step{samples.DeferStep("design"), {Type: "acceptance"}, {Type: "implement"}})
	tr.Propose(childName, samples.ChildText(childName, "i0001", 1, []string{"wip/*"}), "")
	tr.CommitParent("tickets")
	ticketkit.OK(t, tr.Approve())
	tr.StartParent()
	tr.Worktree(childName, "i0001")
	ticketkit.OK(t, tr.Ccnavi("ticket", "start", childName))
	phases := filepath.Join(tr.Approved, "phases", "i0001")
	if skipped {
		ticketkit.Write(t, filepath.Join(phases, "1.skipped"), `{"at": "2026-01-01T00:00:00+0000", "deferred_to": 2}`+"\n")
		ticketkit.Write(t, filepath.Join(phases, "2.reviewed"), `{"at": "2026-01-01T00:00:00+0000"}`+"\n")
	}
	tr.CommitParent("marks")
	return tr
}

func statusText(tr *ticketkit.Tree) string {
	tr.T.Helper()
	return ticketkit.OK(tr.T, tr.Ccnavi("ticket", "status", "i0001")).Stdout
}

func lintText(tr *ticketkit.Tree) string { return tr.Ccnavi("--lint").Stdout }

func closeByHand(tr *ticketkit.Tree) {
	tr.T.Helper()
	if err := mkdirRename(tr.ApprovedPath("doing", childName), tr.ApprovedPath("done", childName)); err != nil {
		tr.T.Fatal(err)
	}
	tr.CommitParent("closed by hand")
}

func TestADoingChildWithALeftReviewedMarkerIsWarnedByLintAndStatus(t *testing.T) {
	t.Parallel()
	tr := childWithMarker(t, "design", samples.On, true, true)
	for _, text := range []string{lintText(tr), statusText(tr)} {
		for _, w := range []string{markerWords, "フェーズ 1（設計）", "ccnavi-git.sh rm .ccnavi/approved/phases/i0001/1.reviewed",
			// マーカーのツリーを、ワークスペースルートからの相対で示す。
			"ツリー .claude/worktrees/i0001で",
			// 取り込み済みの親子だけ push-approved が送る。それ以外はユーザがコミットする。
			"取り込み済みで origin がある親子なら", "ccnavi-user-push-approved.sh i0001", "ユーザが自分でコミットする", "新しいチケットを作り直す"} {
			ticketkit.Has(t, text, w)
		}
	}
	// マーカーは消さない。
	if !ticketkit.Exists(filepath.Join(tr.Approved, "phases", "i0001", "1.reviewed")) {
		t.Error("マーカーを消した")
	}
}

func TestNoReviewedMarkerWarningWhereItDoesNotApply(t *testing.T) {
	t.Parallel()
	for _, c := range []struct {
		name  string
		build func(t *testing.T) *ticketkit.Tree
	}{
		{"without_a_marker", func(t *testing.T) *ticketkit.Tree { return childWithMarker(t, "design", samples.On, false, true) }},
		{"a_closed_child", func(t *testing.T) *ticketkit.Tree {
			tr := childWithMarker(t, "design", samples.On, true, true)
			closeByHand(tr)
			return tr
		}},
		// 調査は review: false。マーカーが残っていても、止める条件に入らないので言わない。
		{"a_phase_that_needs_no_review", func(t *testing.T) *ticketkit.Tree { return childWithMarker(t, "research", samples.Off, true, true) }},
		{"a_review_free_form_without_a_marker", func(t *testing.T) *ticketkit.Tree { return childWithMarker(t, "research", samples.On, false, true) }},
	} {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			tr := c.build(t)
			ticketkit.Lacks(t, lintText(tr), markerWords)
			ticketkit.Lacks(t, statusText(tr), markerWords)
		})
	}
}

func TestTheReviewedMarkerWarningDoesNotStopStartOrTheVerdict(t *testing.T) {
	t.Parallel()
	tr := childWithMarker(t, "design", samples.On, true, false)
	ticketkit.OK(t, tr.Ccnavi("ticket", "start", childName))
	child := tr.ChildTree(childName)
	ticketkit.Lacks(t, tr.WriteReason(child, filepath.Join(child, "wip", "design", "x.md")), "DENY")
	// lint は warn だけで、終了コードを失敗にしない。
	warned := ticketkit.OK(t, tr.Ccnavi("--lint"))
	ticketkit.Has(t, warned.Stdout, markerWords)
	ticketkit.Has(t, warned.Stdout, "error 0 件")
}

func TestAResumedChildOfADeferredPhaseIsWarnedWithTheMarkerPlace(t *testing.T) {
	t.Parallel()
	tr := deferredChild(t, true)
	for _, text := range []string{lintText(tr), statusText(tr)} {
		for _, w := range []string{deferredEnded, ".ccnavi/approved/phases/i0001/ に *.reviewed が残っていれば",
			"ccnavi-git.sh rm .ccnavi/approved/phases/i0001/<番号>.reviewed", "ツリー .claude/worktrees/i0001で", "続きの作業だけなら、そのままで構いません"} {
			ticketkit.Has(t, text, w)
		}
	}
	if !ticketkit.Exists(filepath.Join(tr.Approved, "phases", "i0001", "2.reviewed")) || !ticketkit.Exists(filepath.Join(tr.Approved, "phases", "i0001", "1.skipped")) {
		t.Error("マーカーを消した")
	}
}

func TestNoDeferredPhaseWarningWhereItDoesNotApply(t *testing.T) {
	t.Parallel()
	for _, c := range []struct {
		name  string
		build func(t *testing.T) *ticketkit.Tree
	}{
		{"a_phase_that_has_not_ended", func(t *testing.T) *ticketkit.Tree { return deferredChild(t, false) }},
		{"a_closed_child", func(t *testing.T) *ticketkit.Tree {
			tr := deferredChild(t, true)
			closeByHand(tr)
			return tr
		}},
	} {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			tr := c.build(t)
			lint := lintText(tr)
			ticketkit.Lacks(t, lint, markerWords)
			ticketkit.Lacks(t, lint, "一度終わっています")
			ticketkit.Lacks(t, statusText(tr), "一度終わっています")
		})
	}
}

func TestTheDeferredWarningDoesNotStopTheVerdictOrLint(t *testing.T) {
	t.Parallel()
	tr := deferredChild(t, true)
	child := tr.ChildTree(childName)
	ticketkit.Lacks(t, tr.WriteReason(child, filepath.Join(child, "wip", "design", "x.md")), "DENY")
	warned := ticketkit.OK(t, tr.Ccnavi("--lint"))
	ticketkit.Has(t, warned.Stdout, "一度終わっています")
	ticketkit.Has(t, warned.Stdout, "error 0 件")
}

// 調査は review: false。doing の子は review_required に数えられないが、閉じ直すと要る。
func TestAResumedChildAskingForReviewIsWarnedEvenIfTheKindNeedsNone(t *testing.T) {
	t.Parallel()
	tr := childWithMarker(t, "research", samples.On, true, true)
	for _, text := range []string{lintText(tr), statusText(tr)} {
		ticketkit.Has(t, text, markerWords)
		ticketkit.Has(t, text, "ccnavi-git.sh rm .ccnavi/approved/phases/i0001/1.reviewed")
	}
}
