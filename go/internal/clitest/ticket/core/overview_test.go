package core

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// familyWithChild は親（計画 research・design）と、フェーズ 1 の子 1 つを承認する。
func familyWithChild(t *testing.T) *ticketkit.Tree {
	t.Helper()
	tr := newPhaseTree(t)
	tr.PhaseFamily(samples.Plan("research", "design"))
	tr.Propose("i0001-01-01", samples.ChildText("i0001-01-01", "i0001", 1, []string{"wip/research/*"}), "")
	tr.CommitParent("tickets")
	ticketkit.OK(t, tr.Approve())
	return tr
}

func parentLine(t *testing.T, out string) string {
	t.Helper()
	for _, line := range strings.Split(out, "\n") {
		if strings.HasPrefix(line, "- 親 i0001 ") {
			return line
		}
	}
	t.Fatalf("親の行が無い:\n%s", out)
	return ""
}

// このPCで作業中の親の一覧（`ticket status` に引数なし）。1 行に親・ブランチ・MR（親チケットの mr:）・次の子・未送信を出し、
// 取り込みの時刻（最終取り込み）は出さない。リモート追跡の ref が無ければ「一度も送っていない」、あれば未送信の数を言う。
func TestStatusWithoutAnArgumentListsEachParentInOneLine(t *testing.T) {
	t.Parallel()
	t.Run("1 行の中身", func(t *testing.T) {
		t.Parallel()
		tr := familyWithChild(t)
		out := said(t, tr)
		ticketkit.Has(t, out, "== このPCで作業中の親")
		line := parentLine(t, out)
		ticketkit.Has(t, line, "ブランチ i0001")
		ticketkit.Has(t, line, "MR !1") // 親チケットの mr:
		ticketkit.Has(t, line, "次の子 i0001-01-01")
		ticketkit.Lacks(t, line, "最終取り込み")
		// origin が無いので、リモート追跡の ref が無い。送っていないものは「一度も送っていない」
		ticketkit.Has(t, line, "未送信 一度も送っていないあり")
	})
	t.Run("MR の番号は親チケットだけが決める", func(t *testing.T) {
		t.Parallel()
		tr := newPhaseTree(t)
		tr.PhaseFamily(samples.Plan("research", "design"))
		line := parentLine(t, said(t, tr))
		ticketkit.Has(t, line, "MR !1")
		ticketkit.Lacks(t, line, "リモート未確認")
	})
	t.Run("未送信はリモート追跡の ref と比べて数える", func(t *testing.T) {
		t.Parallel()
		tr := newPhaseTree(t)
		tr.PhaseFamily(samples.Plan("research"))
		tr.PushOrigin()
		ticketkit.Has(t, parentLine(t, said(t, tr)), "未送信 なし")
		ticketkit.Write(t, filepath.Join(tr.ParentTree, "a.txt"), "a")
		tr.Parent("add", "a.txt")
		tr.Parent("commit", "--quiet", "-m", "local work")
		ticketkit.Has(t, parentLine(t, said(t, tr)), "未送信 1 コミットあり")
	})
}

// 親を渡した `ticket status` は、子のワークツリーの状態を出さない（引数なしのときだけ出す）。
func TestStatusOfAParentDoesNotSayTheChildrenOrTheLastSync(t *testing.T) {
	t.Parallel()
	tr := newPhaseTree(t)
	tr.PhaseFamily(samples.Plan("research", "design"))
	out := said(t, tr, "i0001")
	for _, not := range []string{"最終取り込み", "== このPCで作業中の親", "子のワークツリー:"} {
		ticketkit.Lacks(t, out, not)
	}
}

func TestChildWorktreesAreMergedUnmergedOrDirty(t *testing.T) {
	t.Parallel()
	tr := familyWithChild(t)
	child := tr.RunChild("i0001-01-01", ticketkit.File{Path: "wip/research/new.md", Text: "x\n"})
	ticketkit.Has(t, said(t, tr), "子のワークツリー: i0001-01-01  未合流")
	dirty := filepath.Join(child, "wip", "research", "dirty.md")
	ticketkit.Write(t, dirty, "y\n")
	ticketkit.Has(t, said(t, tr), "子のワークツリー: i0001-01-01  未コミットあり")
	if err := os.Remove(dirty); err != nil {
		t.Fatal(err)
	}
	tr.Merge("i0001-01-01")
	ticketkit.Has(t, said(t, tr), "子のワークツリー: i0001-01-01  合流済み")
}

// セッション開始で、このPCで作業中の親の一覧を渡す（親が無ければ渡さない）。
func TestSessionStartListsTheParentsOfThisPC(t *testing.T) {
	t.Parallel()
	tr := newPhaseTree(t)
	session := func() string {
		r := tr.Hook("SessionStart", "", tr.Root, nil)
		ticketkit.OK(t, r)
		return ticketkit.Reason(t, r)
	}
	ticketkit.Lacks(t, session(), "このPCで作業中の親")
	tr.PhaseFamily(samples.Plan("research"))
	text := session()
	ticketkit.Has(t, text, "[ccnavi] このPCで作業中の親:")
	ticketkit.Has(t, text, "- 親 i0001  ブランチ i0001  MR !1")
	ticketkit.Lacks(t, text, "最終取り込み")
	ticketkit.Lacks(t, text, "リモート未確認")
}
