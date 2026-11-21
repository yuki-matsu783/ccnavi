package small

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

// 実行ファイルを起こさずに動く sh（ccnavi-fetch.sh・ccnavi-user-push-approved.sh）が持つ規則の写しが、
// 表どおりに動くこと。実行ファイル側は go test が同じ表で見る（layout.json・approved-carry.json）。

// approvedCarry は tests/fixtures/approved-carry.json。
type approvedCarry struct {
	Porcelain []struct {
		Entry string `json:"entry"`
		Path  string `json:"path"`
	} `json:"porcelain"`
	Temp []struct {
		Path string `json:"path"`
		Temp bool   `json:"temp"`
	} `json:"temp"`
	Under []struct {
		Path string  `json:"path"`
		Base string  `json:"base"`
		Rest *string `json:"rest"`
	} `json:"under"`
	Same []struct {
		A    string `json:"a"`
		B    string `json:"b"`
		Same bool   `json:"same"`
	} `json:"same"`
	GoOnly []struct {
		Path string `json:"path"`
		Base string `json:"base"`
	} `json:"go_only"`
}

// shCommon は ccnavi-common.sh を読んだ sh に、関数を呼ばせて答えを取る（`. "$0"; <fn>`）。
func shCommon(t *testing.T, shell string, o run.Opts, script string, args ...string) run.Result {
	t.Helper()
	return runProgram(t, shell, o, append([]string{"-c", `. "$0"; ` + script, commonSh()}, args...)...)
}

func TestShLayoutMatchesTheTable(t *testing.T) {
	t.Parallel()
	var want struct {
		Worktrees string `json:"worktrees"`
		Projects  string `json:"projects"`
		Approved  string `json:"approved"`
		Proposals string `json:"proposals"`
	}
	loadFixture(t, "layout.json", &want)
	shell := firstShell(t)
	res := shCommon(t, shell, run.Opts{},
		`printf "%s\n" "$ccnavi_worktrees_dir" "$ccnavi_projects_dir" "$ccnavi_approved_dir" "$ccnavi_proposals_dir"`)
	if res.Code != 0 {
		t.Fatalf("code = %d, stderr = %q", res.Code, res.Stderr)
	}
	got := strings.Split(strings.TrimSuffix(res.Stdout, "\n"), "\n")
	wantLines := []string{want.Worktrees, want.Projects, want.Approved, want.Proposals}
	if strings.Join(got, "|") != strings.Join(wantLines, "|") {
		t.Errorf("got %q, want %q", got, wantLines)
	}
}

// TestPorcelainPathsReadTheChangedPaths は、`status --porcelain -z` の項目からパスを読む（ccnavi_porcelain_paths）。
// 2 つの locale で同じ答え。
func TestPorcelainPathsReadTheChangedPaths(t *testing.T) {
	t.Parallel()
	var tab approvedCarry
	loadFixture(t, "approved-carry.json", &tab)
	if len(tab.Porcelain) == 0 {
		t.Fatal("表が空")
	}
	var input strings.Builder
	var want []string
	for _, c := range tab.Porcelain {
		input.WriteString(c.Entry + "\x00")
		want = append(want, c.Path)
	}
	shell := firstShell(t)
	for _, locale := range []string{"C", "C.UTF-8"} {
		t.Run(locale, func(t *testing.T) {
			t.Parallel()
			res := shCommon(t, shell, run.Opts{
				Input: input.String(),
				Env:   map[string]string{"LC_ALL": locale},
			}, "ccnavi_porcelain_paths")
			got := splitLines(res.Stdout)
			if strings.Join(got, "\n") != strings.Join(want, "\n") {
				t.Errorf("got %q, want %q", got, want)
			}
		})
	}
}

// TestDropTempKeepsOnlyRealPaths は、書きかけの一時ファイルをコミットから外す（ccnavi_drop_temp）。
func TestDropTempKeepsOnlyRealPaths(t *testing.T) {
	t.Parallel()
	var tab approvedCarry
	loadFixture(t, "approved-carry.json", &tab)
	if len(tab.Temp) == 0 {
		t.Fatal("表が空")
	}
	var input strings.Builder
	var want []string
	for _, c := range tab.Temp {
		input.WriteString(c.Path + "\n")
		if !c.Temp {
			want = append(want, c.Path)
		}
	}
	res := shCommon(t, firstShell(t), run.Opts{Input: input.String()}, "ccnavi_drop_temp")
	got := splitLines(res.Stdout)
	if strings.Join(got, "\n") != strings.Join(want, "\n") {
		t.Errorf("got %q, want %q", got, want)
	}
}

// TestPathUnderIsCaseFoldedOnlyForASCII は、パスが基準の下にあるか（ccnavi_path_under）。
// ASCII の英字だけ畳む。ASCII の外の字の大文字小文字は、sh では畳まない（実行ファイルだけが同じとみなす）。
func TestPathUnderIsCaseFoldedOnlyForASCII(t *testing.T) {
	t.Parallel()
	var tab approvedCarry
	loadFixture(t, "approved-carry.json", &tab)
	if len(tab.Under) == 0 {
		t.Fatal("表が空")
	}
	shell := firstShell(t)
	for _, locale := range []string{"C", "C.UTF-8"} {
		for _, c := range tab.Under {
			t.Run(locale+"/"+c.Path+"/"+c.Base, func(t *testing.T) {
				t.Parallel()
				res := shCommon(t, shell, run.Opts{Env: map[string]string{"LC_ALL": locale}},
					`ccnavi_path_under "$1" "$2"`, c.Path, c.Base)
				if c.Rest == nil {
					if res.Code == 0 {
						t.Errorf("下に無いはずのパスが通った: stdout %q", res.Stdout)
					}
					return
				}
				if res.Code != 0 {
					t.Fatalf("code = %d, stderr = %q", res.Code, res.Stderr)
				}
				if got := strings.TrimRight(res.Stdout, "\n"); got != *c.Rest {
					t.Errorf("rest = %q, want %q", got, *c.Rest)
				}
			})
		}
	}
	for _, c := range tab.GoOnly {
		t.Run("go_only/"+c.Path, func(t *testing.T) {
			t.Parallel()
			res := shCommon(t, shell, run.Opts{}, `ccnavi_path_under "$1" "$2"`, c.Path, c.Base)
			if res.Code == 0 {
				t.Errorf("sh が ASCII の外の字を畳んだ: %q", c.Path)
			}
		})
	}
}

// TestPathPickComparesNamesLikeTheExecutable は、変更のパスと渡したパスが同じか（ccnavi_path_pick）。
func TestPathPickComparesNamesLikeTheExecutable(t *testing.T) {
	t.Parallel()
	var tab approvedCarry
	loadFixture(t, "approved-carry.json", &tab)
	if len(tab.Same) == 0 {
		t.Fatal("表が空")
	}
	shell := firstShell(t)
	for _, c := range tab.Same {
		t.Run(c.A+"_"+c.B, func(t *testing.T) {
			t.Parallel()
			// 同じ木の中で並行に書き換えないよう、テストごとに別の置き場を使う。
			file := filepath.Join(base.TempDir(t), "want")
			if err := os.WriteFile(file, []byte(c.A+"\n"), 0o644); err != nil {
				t.Fatal(err)
			}
			res := shCommon(t, shell, run.Opts{Input: c.B + "\n"}, `ccnavi_path_pick "$1"`, file)
			got := splitLines(res.Stdout)
			var wantLines []string
			if c.Same {
				wantLines = []string{c.B}
			}
			if strings.Join(got, "\n") != strings.Join(wantLines, "\n") {
				t.Errorf("got %q, want %q", got, wantLines)
			}
		})
	}
}
