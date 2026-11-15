package propose

import (
	"path/filepath"
	"testing"

	"ccnavi/internal/tickets/testkit"
)

// 送る提案は、この親子の todo/ の .md（拡張子の大文字小文字を問わない）とフローの下書きだけ。名前が親で始まる
// だけの別の親（i0001x）とほかの親は入れない。
func TestCandidatesPickOnlyThisFamily(t *testing.T) {
	base := t.TempDir()
	for _, rel := range []string{"todo/i0001.MD", "todo/i0001-01-01.md", "todo/i0001x.md", "todo/i0002.md",
		"todo/i0001.txt", "flows/i0001-01-01.yml", "flows/i0002-01-01.yml"} {
		testkit.Put(t, filepath.Join(base, "wip", "proposals", filepath.FromSlash(rel)), "x\n")
	}
	var got []string
	for _, p := range candidates(base, "wip/proposals", "i0001") {
		rel, _ := filepath.Rel(filepath.Join(base, "wip", "proposals"), p)
		got = append(got, filepath.ToSlash(rel))
	}
	want := []string{"todo/i0001-01-01.md", "todo/i0001.MD", "flows/i0001-01-01.yml"}
	if len(got) != len(want) {
		t.Fatalf("got %v", got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("got %v, want %v", got, want)
		}
	}
}

// 計画の検査は親の提案（todo/<親>.md）だけにかける。子の提案・計画の無い提案・読めない提案は見ない。
func TestPlanProblemsOnlyForTheParentsOwnProposal(t *testing.T) {
	base := t.TempDir()
	bad := testkit.ProposalParent("i0001", []string{"  - research", "  - {type: design, after: [5]}"})
	child := testkit.Put(t, filepath.Join(base, "todo", "i0001-01-01.md"), bad)
	if got := planProblems([]string{child}, "i0001"); len(got) != 0 {
		t.Errorf("子の提案を検査した: %v", got)
	}
	free := testkit.Put(t, filepath.Join(base, "todo", "i0001.md"), "plan も feedback も無い\n")
	if got := planProblems([]string{free}, "i0001"); len(got) != 0 {
		t.Errorf("計画の無い提案を検査した: %v", got)
	}
	testkit.Put(t, free, bad)
	if got := planProblems([]string{free}, "i0001"); len(got) == 0 {
		t.Error("親の提案の誤りを見逃した")
	}
}
