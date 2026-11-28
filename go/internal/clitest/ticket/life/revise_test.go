package life

import (
	"os"
	"regexp"
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/ticketkit"
)

// `ccnavi ticket revise`（承認済みの親の改版の雛形）。
//  1. --feedback は、承認済みチケットのコピーから、スクリプトだけが書く欄を外し、feedback: [] を足す。書いた雛形は承認の確認
//     （--agree --preview --verify）を通り、承認できる
//  2. 全体計画のレビューが済む前は、--feedback は何も書かずに止まる
//  3. --feedback を付けないと、写しだけを作る（feedback: は足さない）
//  4. todo/ に同じ識別子の提案が既にあれば、上書きしない。承認済みの親が無い・cwd が親のワークツリーの外・子の識別子は、何も書かずに止まる
//  5. 親をクローズできない理由（finish）が、この雛形の作り方を言う

// スクリプトだけが書く欄。写しでは空にする。
var scriptFields = []string{"started_at", "completed_at", "base_sha", "merged_sha", "cancelled_at", "cancel_reason", "finished_to", "settled_by"}

func revise(tr *ticketkit.Tree, ident, cwd string, extra ...string) run.Result {
	tr.T.Helper()
	if ident == "" {
		ident = "i0001"
	}
	if cwd == "" {
		cwd = tr.ParentTree
	}
	args := append([]string{"ticket", "revise", ident}, extra...)
	return tr.Ccnavi(append(args, "--cwd", cwd)...)
}

// reviewedTree は全体計画のレビューが済み、親が着手済みの木。
func reviewedTree(t *testing.T) *ticketkit.Tree {
	t.Helper()
	tr := waitingTree(t)
	tr.StartParent()
	return tr
}

func TestFeedbackMakesAScaffoldThatPassesTheApprovalCheck(t *testing.T) {
	t.Parallel()
	tr := reviewedTree(t)
	made := ticketkit.OK(t, revise(tr, "", "", "--feedback"))
	text := ticketkit.Read(t, tr.TodoPath("i0001"))
	ticketkit.Has(t, text, "feedback: []")
	for _, name := range scriptFields {
		if regexp.MustCompile(`(?m)^` + name + `: \S`).MatchString(text) {
			t.Errorf("%s に値が残っている", name)
		}
	}
	ticketkit.Has(t, made.Stdout, "approval-request i0001 --check")
	next := 0
	for _, l := range ticketkit.Lines(made.Stdout) {
		if strings.HasPrefix(l, "次: ") {
			next++
		}
	}
	if next != 1 {
		t.Errorf("「次:」が %d 行", next)
	}
	tr.CommitParent("revise")
	ticketkit.OK(t, tr.Ccnavi("--agree", "--preview", "--verify", "i0001"))
	ticketkit.OK(t, tr.Agree("y"))
	ticketkit.Has(t, ticketkit.Read(t, tr.ApprovedPath("doing", "i0001")), "feedback: []")
}

func TestFeedbackIsRefusedBeforeTheLastReviewIsDone(t *testing.T) {
	t.Parallel()
	tr, _ := requestedTree(t)
	tr.StartParent()
	refused := ticketkit.Fails(t, revise(tr, "", "", "--feedback"))
	ticketkit.Has(t, refused.Stderr, "レビューが済んでから")
	if ticketkit.Exists(tr.TodoPath("i0001")) {
		t.Error("雛形を書いた")
	}
}

func TestFeedbackIsRefusedWhenTheParentAlreadyHasOne(t *testing.T) {
	t.Parallel()
	tr := reviewedTree(t)
	ticketkit.OK(t, revise(tr, "", "", "--feedback"))
	tr.CommitParent("revise")
	ticketkit.OK(t, tr.Agree("y"))
	_ = os.Remove(tr.TodoPath("i0001"))
	refused := ticketkit.Fails(t, revise(tr, "", "", "--feedback"))
	ticketkit.Has(t, refused.Stderr, "既にある")
}

func TestWithoutTheFlagOnlyTheCopyIsMade(t *testing.T) {
	t.Parallel()
	tr := reviewedTree(t)
	made := ticketkit.OK(t, revise(tr, "", ""))
	ticketkit.Lacks(t, ticketkit.Read(t, tr.TodoPath("i0001")), "feedback:")
	ticketkit.Has(t, made.Stdout, "plan・phases・allow などを直す")
	// 次の一手は 1 つで、注釈とは分かれている。
	lines := ticketkit.Lines(made.Stdout)
	next := 0
	var notes []string
	for _, l := range lines {
		if strings.HasPrefix(l, "次: ") {
			next++
		}
		if strings.HasPrefix(l, "案内: ") {
			notes = append(notes, l)
		}
	}
	if next != 1 {
		t.Errorf("「次:」が %d 行:\n%s", next, made.Stdout)
	}
	all := strings.Join(notes, "\n")
	ticketkit.Has(t, all, "ccnavi-ticket.sh cancel <子>")
	ticketkit.Has(t, all, "approval-request i0001 --check")
}

func TestAnExistingProposalIsNotOverwritten(t *testing.T) {
	t.Parallel()
	tr := reviewedTree(t)
	ticketkit.OK(t, revise(tr, "", ""))
	f, err := os.OpenFile(tr.TodoPath("i0001"), os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.WriteString("\n書きかけ\n"); err != nil {
		t.Fatal(err)
	}
	if err := f.Close(); err != nil {
		t.Fatal(err)
	}
	again := ticketkit.Fails(t, revise(tr, "", ""))
	ticketkit.Has(t, again.Stderr, "既にある")
	ticketkit.Has(t, ticketkit.Read(t, tr.TodoPath("i0001")), "書きかけ")
}

func TestNoApprovedParentAChildIdAndAWrongCwdStop(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	mustRemove(t, tr.ApprovedPath("doing", "i0001"))
	ticketkit.Has(t, revise(tr, "", "").Stderr, "承認済みの親が")
	ticketkit.Has(t, revise(tr, "i0001-01-01", "").Stderr, "子の識別子")
	ticketkit.Has(t, revise(tr, "", tr.Root).Stderr, "の中ではない")
	if ticketkit.Exists(tr.TodoPath("i0001")) {
		t.Error("何も書かないはずが書いた")
	}
}

func TestFinishSaysHowToMakeTheFeedbackScaffold(t *testing.T) {
	t.Parallel()
	tr := reviewedTree(t)
	refused := ticketkit.Fails(t, tr.Ccnavi("ticket", "finish", "i0001"))
	ticketkit.Has(t, refused.Stderr, "ticket revise i0001 --feedback")
}
