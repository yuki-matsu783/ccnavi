package tree

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const directName = "fix-1-p-x"

type directFixture struct {
	ws, p, q, wt string
	main         Tree
}

func newDirectFixture(t *testing.T) directFixture {
	ws := realTemp(t)
	initRepo(t, ws)
	p := filepath.Join(ws, "projects", "p")
	q := filepath.Join(ws, "projects", "q")
	initRepo(t, p)
	initRepo(t, q)
	wt := filepath.Join(ws, ".claude", "worktrees", directName)
	git(t, p, "worktree", "add", "--quiet", wt, "-b", directName)
	return directFixture{ws, p, q, wt, Tree{Name: "p", Root: p, Project: "p", Kind: KindProject}}
}

func (f directFixture) record(t *testing.T, side, common, main string) {
	t.Helper()
	if common == "" {
		common = filepath.Join(f.p, ".git")
	}
	if main == "" {
		main = f.p
	}
	path := filepath.Join(f.p, ".git", DirectDir, directName)
	os.MkdirAll(filepath.Dir(path), 0o755)
	text := strings.Join([]string{"version 4", "base_branch main", "side_by_side " + side, "main_tree " + main, "common_dir " + common, ""}, "\n")
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
}

func names(records []DirectRecord) []string {
	var out []string
	for _, r := range records {
		out = append(out, r.Name)
	}
	return out
}

func TestDirectRecords(t *testing.T) {
	f := newDirectFixture(t)
	f.record(t, "yes", "", "")
	if got := DirectRecords(f.ws, f.main); len(got) != 1 || got[0].Name != directName || got[0].baseBranch != "main" {
		t.Fatalf("有効な記録 = %+v", got)
	}
	if roots := SideBySideRoots(f.ws, filepath.Join(f.ws, "projects")); len(roots) != 1 || roots[0].Name != "p" {
		t.Errorf("SideBySideRoots = %+v", roots)
	}

	invalid := []struct {
		name               string
		side, common, main string
	}{
		{"指定が無い", "no", "", ""},
		{"共通ディレクトリが違う", "yes", filepath.Join(f.q, ".git"), ""},
		{"チェックアウトが違う", "yes", "", f.q},
	}
	for _, c := range invalid {
		f.record(t, c.side, c.common, c.main)
		if got := DirectRecords(f.ws, f.main); len(got) != 0 {
			t.Errorf("%s: %q", c.name, names(got))
		}
	}

	f.record(t, "yes", "", "")
	git(t, f.wt, "checkout", "--quiet", "-b", "other")
	if got := DirectRecords(f.ws, f.main); len(got) != 0 {
		t.Errorf("別のブランチを開いたワークツリー: %q", names(got))
	}
	git(t, f.p, "worktree", "remove", "--force", f.wt)
	if got := DirectRecords(f.ws, f.main); len(got) != 0 {
		t.Errorf("ワークツリーを消した: %q", names(got))
	}
}

func TestParseDirect(t *testing.T) {
	got := ParseDirect("version 3\r\n\n  side_by_side  yes \nversion 4\nlonely\n")
	want := map[string]string{"version": "4", "side_by_side": "yes", "lonely": ""}
	if len(got) != len(want) {
		t.Fatalf("ParseDirect = %q", got)
	}
	for k, v := range want {
		if got[k] != v {
			t.Errorf("ParseDirect[%q] = %q, want %q", k, got[k], v)
		}
	}
}
