package life

import (
	"os"
	"path/filepath"
	"testing"

	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// 識別子と違う名前のワークツリーで親を承認すると、ready まで進んで止まる（issue #334）。実装が終わってから気づくと手戻りが大きいので、
// 早い段階で止める。
//  1. 承認: 親（子）の提案が識別子と違う名前のワークツリーに在れば、承認しない（error）
//  2. 着手・完了: 承認済みチケットが識別子と違う名前のワークツリーに在れば、start・finish が止まる
//  3. 案内: 止めた文面に、直す手順（同名のワークツリーを切り、提案を移し、承認を取り直す）が出る（文面は本体の approval のテスト）

// phaseFamily は定義の見本（samples.Phases）の木で、親（計画 research・design）を承認し、子を 2 本とも持たない形（Cached）。
func phaseFamily(t *testing.T) *ticketkit.Tree {
	t.Helper()
	return ticketkit.Cached(t, "phase-family-research-design", ticketkit.Options{Phases: samples.Phases}, func(tr *ticketkit.Tree) {
		tr.PhaseFamily(samples.Plan("research", "design"))
	})
}

func commitIn(tr *ticketkit.Tree, tree, message string) {
	tr.T.Helper()
	tr.Git(tree, "add", "-A")
	tr.Git(tree, "commit", "--quiet", "-m", message)
}

// moveApprovedTo は承認済みチケットの置き場ごと、別のツリーへ動かす（別名のツリーで承認した形を作る）。
func moveApprovedTo(tr *ticketkit.Tree, tree string) {
	tr.T.Helper()
	if err := os.Rename(filepath.Join(tr.ParentTree, ".ccnavi"), filepath.Join(tree, ".ccnavi")); err != nil {
		tr.T.Fatal(err)
	}
}

func TestAParentProposalInADifferentlyNamedWorktreeIsNotApproved(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t, ticketkit.Options{Phases: samples.Phases})
	stray := tr.Worktree("skill-proposals", "main")
	tr.Propose("i0003", samples.ParentText("i0003", samples.Plan("research"), samples.ParentOpts{Copy: tr.Phases}), stray)
	commitIn(tr, stray, "approval-request i0003")
	r := ticketkit.Fails(t, tr.Agree("y"))
	if r.Code != 1 {
		t.Errorf("code %d", r.Code)
	}
	said := ticketkit.Out(r)
	for _, w := range []string{"提案 i0003 を識別子と違う名前のワークツリー skill-proposals", "ccnavi-worktree.sh i0003", "承認の前に直す"} {
		ticketkit.Has(t, said, w)
	}
	if ticketkit.Exists(filepath.Join(stray, ".ccnavi", "approved", "doing", "i0003.md")) {
		t.Error("承認しないはずが置いた")
	}
}

func TestAParentProposalInItsOwnWorktreeIsApproved(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t, ticketkit.Options{Phases: samples.Phases})
	own := tr.Worktree("i0003", "main")
	tr.Propose("i0003", samples.ParentText("i0003", samples.Plan("research"), samples.ParentOpts{Copy: tr.Phases}), own)
	commitIn(tr, own, "approval-request i0003")
	ticketkit.OK(t, tr.Agree("y"))
	if !ticketkit.Exists(filepath.Join(own, ".ccnavi", "approved", "doing", "i0003.md")) {
		t.Error("承認済みの置き場に無い")
	}
}

func TestAChildProposalOutsideTheParentsWorktreeIsNotApproved(t *testing.T) {
	t.Parallel()
	tr := phaseFamily(t)
	stray := tr.Worktree("skill-proposals", "main")
	tr.Propose(childName, samples.ChildText(childName, "i0001", 1, []string{"wip/design/*"}), stray)
	commitIn(tr, stray, "approval-request child")
	r := ticketkit.Fails(t, tr.Agree("y"))
	said := ticketkit.Out(r)
	ticketkit.Has(t, said, "子の提案 i0001-01-01 を識別子と違う名前のワークツリー skill-proposals")
	ticketkit.Has(t, said, "ccnavi-worktree.sh i0001")
}

func TestStartAndFinishStopWhenTheApprovedTicketIsInAnotherWorktree(t *testing.T) {
	t.Parallel()
	t.Run("start", func(t *testing.T) {
		t.Parallel()
		tr := phaseFamily(t)
		stray := tr.Worktree("skill-proposals", "main")
		moveApprovedTo(tr, stray)
		r := ticketkit.Fails(t, tr.Ccnavi("ticket", "start", "i0001"))
		ticketkit.Has(t, ticketkit.Out(r), "ワークツリーの外")
		ticketkit.Has(t, ticketkit.Out(r), "skill-proposals")
	})
	t.Run("finish", func(t *testing.T) {
		t.Parallel()
		tr := phaseFamily(t)
		ticketkit.OK(t, tr.Ccnavi("ticket", "start", "i0001"))
		stray := tr.Worktree("skill-proposals", "main")
		moveApprovedTo(tr, stray)
		r := ticketkit.Fails(t, tr.Ccnavi("ticket", "finish", "i0001"))
		ticketkit.Has(t, ticketkit.Out(r), "ワークツリーの外")
		// 止めたので、閉じたチケットは別名のツリーの doing/ に残る（done/ へ動かない）。
		if !ticketkit.Exists(filepath.Join(stray, ".ccnavi", "approved", "doing", "i0001.md")) {
			t.Error("別名のツリーの doing/ から消えた")
		}
	})
}

func TestStartPassesInTheParentWorktree(t *testing.T) {
	t.Parallel()
	tr := phaseFamily(t)
	ticketkit.OK(t, tr.Ccnavi("ticket", "start", "i0001"))
}
