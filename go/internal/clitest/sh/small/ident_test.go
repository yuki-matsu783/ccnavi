package small

import (
	"testing"

	"ccnavi/internal/harness/base"
)

// identTable は tests/fixtures/ident-cases.json（識別子・親のブランチ名・保護されたブランチ）。
// 実行ファイルの答えは ids のテストが同じ表で見る。
type identTable struct {
	IdentGood   []string `json:"ident_good"`
	IdentBad    []string `json:"ident_bad"`
	ShOnly      []string `json:"sh_only"`
	BranchGood  []string `json:"branch_good"`
	BranchBad   []string `json:"branch_bad"`
	Protected   []string `json:"protected"`
	Unprotected []string `json:"unprotected"`
}

// TestIdentIsTheSameInEveryShellAndLocale は、ccnavi_is_ident の答えが sh・bash と C・C.UTF-8 で同じこと、
// 前の形（`i0055-01`）も通ることを見る。ASCII の外の字の種類は実行ファイルが止める（sh は通す）。
func TestIdentIsTheSameInEveryShellAndLocale(t *testing.T) {
	t.Parallel()
	var tab identTable
	loadFixture(t, "ident-cases.json", &tab)
	shells := shellNames()
	if len(shells) == 0 {
		t.Skip("sh が無い")
	}
	dir := base.TempDir(t)
	for _, shell := range shells {
		for _, locale := range []string{"C", "C.UTF-8"} {
			for _, v := range tab.IdentGood {
				t.Run(shell+"/"+locale+"/good/"+v, func(t *testing.T) {
					t.Parallel()
					if !probe(t, shell, dir, "ccnavi_is_ident", v, locale) {
						t.Errorf("通すべき識別子が止まった: %q", v)
					}
				})
			}
			for _, v := range tab.IdentBad {
				t.Run(shell+"/"+locale+"/bad/"+v, func(t *testing.T) {
					t.Parallel()
					if probe(t, shell, dir, "ccnavi_is_ident", v, locale) {
						t.Errorf("止めるべき識別子が通った: %q", v)
					}
				})
			}
		}
	}
	for _, v := range tab.ShOnly {
		t.Run("sh_only/"+v, func(t *testing.T) {
			t.Parallel()
			if !probe(t, shells[0], dir, "ccnavi_is_ident", v, "C") {
				t.Errorf("sh は通すべき字が止まった: %q", v)
			}
		})
	}
}

// TestBranchNameIsTheSameInEveryShellAndLocale は、親のブランチ名（ccnavi_is_branch）を見る。
func TestBranchNameIsTheSameInEveryShellAndLocale(t *testing.T) {
	t.Parallel()
	var tab identTable
	loadFixture(t, "ident-cases.json", &tab)
	shells := shellNames()
	if len(shells) == 0 {
		t.Skip("sh が無い")
	}
	dir := base.TempDir(t)
	for _, shell := range shells {
		for _, locale := range []string{"C", "C.UTF-8"} {
			for _, v := range tab.BranchGood {
				t.Run(shell+"/"+locale+"/good/"+v, func(t *testing.T) {
					t.Parallel()
					if !probe(t, shell, dir, "ccnavi_is_branch", v, locale) {
						t.Errorf("通すべきブランチ名が止まった: %q", v)
					}
				})
			}
			for _, v := range tab.BranchBad {
				t.Run(shell+"/"+locale+"/bad/"+v, func(t *testing.T) {
					t.Parallel()
					if probe(t, shell, dir, "ccnavi_is_branch", v, locale) {
						t.Errorf("止めるべきブランチ名が通った: %q", v)
					}
				})
			}
		}
	}
}

// TestProtectedBranchesAreRecognised は、保護されたブランチ（ccnavi_is_protected）。実行ファイルの ids.Protected と同じ表。
func TestProtectedBranchesAreRecognised(t *testing.T) {
	t.Parallel()
	var tab identTable
	loadFixture(t, "ident-cases.json", &tab)
	shells := shellNames()
	if len(shells) == 0 {
		t.Skip("sh が無い")
	}
	dir := base.TempDir(t)
	for _, shell := range shells {
		for _, v := range tab.Protected {
			t.Run(shell+"/protected/"+v, func(t *testing.T) {
				t.Parallel()
				if !probe(t, shell, dir, "ccnavi_is_protected", v, "C") {
					t.Errorf("保護されるべきブランチが通った: %q", v)
				}
			})
		}
		for _, v := range tab.Unprotected {
			t.Run(shell+"/unprotected/"+v, func(t *testing.T) {
				t.Parallel()
				if probe(t, shell, dir, "ccnavi_is_protected", v, "C") {
					t.Errorf("保護されないはずのブランチが止まった: %q", v)
				}
			})
		}
	}
}
