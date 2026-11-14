package ids

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"ccnavi/internal/tickets/testkit"
)

// sh（ccnavi_is_ident・ccnavi_is_branch）と同じ表（tests/fixtures/ident-cases.json）で、実行ファイルの
// 答えを確かめる。sh が通す識別子は実行ファイルも通し、sh_only は実行ファイルだけが止める（2 つ目の守り）。
// ident_bad は sh だけの答え（`..` のように実行ファイルは識別子として通すものがある）。
func TestIdentCasesTable(t *testing.T) {
	data, err := os.ReadFile(filepath.Join(testkit.RepoRoot(t), "tests", "fixtures", "ident-cases.json"))
	if err != nil {
		t.Fatal(err)
	}
	var table struct {
		IdentGood  []string `json:"ident_good"`
		ShOnly     []string `json:"sh_only"`
		BranchGood []string `json:"branch_good"`
		BranchBad  []string `json:"branch_bad"`
	}
	if err := json.Unmarshal(data, &table); err != nil {
		t.Fatal(err)
	}
	if len(table.IdentGood) == 0 || len(table.BranchGood) == 0 {
		t.Fatal("表が空")
	}
	for _, v := range table.IdentGood {
		if !IsValidID(v) {
			t.Errorf("識別子 %q を止めた: %s", v, IDProblem(v))
		}
	}
	for _, v := range table.ShOnly {
		if IsValidID(v) {
			t.Errorf("識別子 %q を通した", v)
		}
	}
	for _, v := range table.BranchGood {
		if p := BranchProblem(v); p != "" {
			t.Errorf("ブランチ名 %q を止めた: %s", v, p)
		}
	}
	for _, v := range table.BranchBad {
		if BranchProblem(v) == "" {
			t.Errorf("ブランチ名 %q を通した", v)
		}
	}
}
