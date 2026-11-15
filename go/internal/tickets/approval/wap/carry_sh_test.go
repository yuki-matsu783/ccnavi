package wap

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/pathcase"
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

// sh（ccnavi_path_under・ccnavi_path_pick）と同じ表で、pathcase.Under と Fold の比べを確かめる。go_only は ASCII の外の字の
// 大文字小文字で、実行ファイルだけが同じとみなす。
func TestApprovedCarryPathCase(t *testing.T) {
	data, err := os.ReadFile(filepath.Join(testkit.RepoRoot(t), "tests", "fixtures", "approved-carry.json"))
	if err != nil {
		t.Fatal(err)
	}
	type under struct {
		Path, Base string
		Rest       *string
	}
	var table struct {
		Under  []under `json:"under"`
		GoOnly []under `json:"go_only"`
		Same   []struct {
			A, B string
			Same bool
		} `json:"same"`
	}
	if err := json.Unmarshal(data, &table); err != nil {
		t.Fatal(err)
	}
	if len(table.Under) == 0 || len(table.Same) == 0 || len(table.GoOnly) == 0 {
		t.Fatal("表が空")
	}
	for _, c := range append(table.Under, table.GoOnly...) {
		rest, ok := pathcase.Under(filepath.FromSlash(c.Path), filepath.FromSlash(c.Base))
		switch {
		case c.Rest == nil && ok:
			t.Errorf("%q は %q の下ではないのに %q", c.Path, c.Base, rest)
		case c.Rest != nil && (!ok || filepath.ToSlash(rest) != *c.Rest):
			t.Errorf("%q を %q から見ると %q（%v）、表は %q", c.Path, c.Base, rest, ok, *c.Rest)
		}
	}
	for _, c := range table.Same {
		if got := pathcase.Fold(c.A) == pathcase.Fold(c.B); got != c.Same {
			t.Errorf("%q と %q: %v", c.A, c.B, got)
		}
	}
}
