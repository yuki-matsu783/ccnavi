package flow

// 承認の時刻。承認は承認済みチケットに時刻を書かないので、表示（ボードの JSON と --explain）は
// doing/ に足したコミット → 「未コミット（手で置いた）」の順で引く。判定の拒否文には時刻を出さない。
// 元: tests/ticket/test_approved_time.py。

import (
	"path/filepath"
	"strings"
	"testing"
)

// researchFamily は親を research 1 ステップで承認した木（写し）。
func researchFamily(t *testing.T) *Tree {
	t.Helper()
	return cachedTree(t, "research-family", func(tr *Tree) {
		tr.FamilyPlan(planOf("research"), nil)
	})
}

func (tr *Tree) boardCopy(ident string) map[string]any {
	tr.T.Helper()
	board := ParseJSON(tr.T, tr.Ccnavi("--explain", "--json").Stdout)
	for _, tk := range jl(board, "tickets") {
		if js(tk, "ticket") == ident {
			return jm(tk, "copy")
		}
	}
	tr.T.Fatalf("%s がボードに無い", ident)
	return nil
}

// moveByHand は GitHub の画面での移動と同じく、提案を doing/ へ動かす。
func (tr *Tree) moveByHand(ident, text string, commit bool) {
	tr.T.Helper()
	Write(tr.T, tr.ApprovedPath("doing", ident), text)
	if commit {
		tr.CommitParent("Rename " + ident)
	}
}

func TestACommittedManualApprovalReadsTheCommitTime(t *testing.T) {
	t.Parallel()
	tr := researchFamily(t)
	tr.moveByHand("i0001-01-01", childNoReview("i0001-01-01", 1, "wip/research/*"), true)
	when := strings.TrimSpace(tr.Parent("log", "-1", "--format=%cI"))
	copy := tr.boardCopy("i0001-01-01")
	if js(copy, "approved_at") != when || js(copy, "approved_from") != "commit" {
		t.Errorf("copy = %v, want approved_at %q from commit", copy, when)
	}
}

// GitHub の画面での移動は rename のコミットになる。--no-renames で足したことにする。
func TestARenameCommitCountsAsAdded(t *testing.T) {
	t.Parallel()
	tr := researchFamily(t)
	tr.Propose("i0001-01-01", childNoReview("i0001-01-01", 1, "wip/research/*"), "")
	tr.CommitParent("approval-request")
	tr.Parent("mv", filepath.Join("wip", "proposals", "todo", "i0001-01-01.md"), filepath.Join(".ccnavi", "approved", "doing", "i0001-01-01.md"))
	tr.CommitParent("Rename")
	Has(t, tr.Parent("show", "--stat", "-M", "--format=", "HEAD"), "=>")
	if got := js(tr.boardCopy("i0001-01-01"), "approved_from"); got != "commit" {
		t.Errorf("approved_from = %q", got)
	}
}

// 承認済みチケットを消したあとに手で動かして承認し直したら、コミットの時刻を読む。
func TestAReapprovalAfterARemovalReadsTheCommitTime(t *testing.T) {
	t.Parallel()
	tr := researchFamily(t)
	text := Read(t, tr.ApprovedPath("doing", "i0001"))
	removeFile(t, tr.ApprovedPath("doing", "i0001"))
	tr.CommitParent("removed")
	tr.moveByHand("i0001", text, true)
	when := strings.TrimSpace(tr.Parent("log", "-1", "--format=%cI"))
	copy := tr.boardCopy("i0001")
	if js(copy, "approved_from") != "commit" || js(copy, "approved_at") != when {
		t.Errorf("copy = %v, want commit %q", copy, when)
	}
}

func TestAnUncommittedManualApprovalIsSaidSo(t *testing.T) {
	t.Parallel()
	tr := researchFamily(t)
	tr.moveByHand("i0001-01-01", childNoReview("i0001-01-01", 1, "wip/research/*"), false)
	copy := tr.boardCopy("i0001-01-01")
	if js(copy, "approved_at") != "" || js(copy, "approved_from") != "uncommitted" {
		t.Errorf("copy = %v", copy)
	}
	shown := tr.Ccnavi("--explain").Stdout
	Has(t, shown, "i0001-01-01（")
	Has(t, shown, "承認 未コミット（手で置いた）")
}

func TestTheRefusalTextDoesNotCarryTheApprovalTime(t *testing.T) {
	t.Parallel()
	tr := researchFamily(t)
	r := tr.Hook("PreToolUse", "Write", tr.ParentTree, map[string]any{"file_path": filepath.Join(tr.ParentTree, "outside", "x.md"), "content": "x\n"})
	said := Out(r)
	_, after, ok := strings.Cut(said, "ticket: i0001")
	if !ok {
		t.Fatalf("ticket: i0001 が無い: %s", said)
	}
	line, _, _ := strings.Cut(after, "\n")
	Lacks(t, line, "approved")
}
