package wap

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"ccnavi/internal/infra/git"
	"ccnavi/internal/tickets/testkit"
)

// sh（ccnavi_porcelain_paths・ccnavi_drop_temp）と同じ表（tests/fixtures/approved-carry.json）で、置き場の変更の
// パスの読み（git.PorcelainPaths）と書きかけの一時ファイル（temp）を確かめる。
func TestApprovedCarryTable(t *testing.T) {
	data, err := os.ReadFile(filepath.Join(testkit.RepoRoot(t), "tests", "fixtures", "approved-carry.json"))
	if err != nil {
		t.Fatal(err)
	}
	var table struct {
		Porcelain []struct{ Entry, Path string } `json:"porcelain"`
		Temp      []struct {
			Path string
			Temp bool
		} `json:"temp"`
	}
	if err := json.Unmarshal(data, &table); err != nil {
		t.Fatal(err)
	}
	if len(table.Porcelain) == 0 || len(table.Temp) == 0 {
		t.Fatal("表が空")
	}
	for _, c := range table.Porcelain {
		if got := git.PorcelainPaths(c.Entry + "\x00"); len(got) != 1 || got[0] != c.Path {
			t.Errorf("%q: %q", c.Entry, got)
		}
	}
	for _, c := range table.Temp {
		if got := temp.MatchString(c.Path); got != c.Temp {
			t.Errorf("%q: 一時ファイル %v", c.Path, got)
		}
	}
}
