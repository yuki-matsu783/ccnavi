package small

import (
	"testing"

	"ccnavi/internal/harness/run"
)

// TestBranchToIDMatchesTheTable は、sh の ccnavi_branch_to_id が親のブランチ名の表（branch-to-id.json）と一致すること。
// 実行ファイル（ids.BranchToID）と拡張（branchToId）も同じ表を読む。
func TestBranchToIDMatchesTheTable(t *testing.T) {
	t.Parallel()
	var tab struct {
		Convert []struct {
			Branch string `json:"branch"`
			ID     string `json:"id"`
		} `json:"convert"`
	}
	loadFixture(t, "branch-to-id.json", &tab)
	shells := shellNames()
	if len(shells) == 0 {
		t.Skip("sh が無い")
	}
	common := commonSh()
	for _, shell := range shells {
		for _, c := range tab.Convert {
			t.Run(shell+"/"+c.Branch, func(t *testing.T) {
				t.Parallel()
				res := runProgram(t, shell, run.Opts{
					Env: map[string]string{"LC_ALL": "C"},
				}, "-c", `. "$0"; ccnavi_branch_to_id "$1"`, common, c.Branch)
				if got := trimNewlines(res.Stdout); got != c.ID {
					t.Errorf("got %q, want %q", got, c.ID)
				}
			})
		}
	}
}

// trimNewlines は末尾の改行をすべて外す（Python の rstrip("\n")）。
func trimNewlines(s string) string {
	for len(s) > 0 && s[len(s)-1] == '\n' {
		s = s[:len(s)-1]
	}
	return s
}
