package reads

import (
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"testing"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/pyjson"
)

type fixture struct {
	root, state, child string
}

// newFixture はワークスペース（git）と、子のワークツリー c1 を作る。どちらにも a.go と docs/b.md を置く。
func newFixture(t *testing.T) fixture {
	t.Helper()
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git が無い")
	}
	root, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	git := func(dir string, args ...string) {
		t.Helper()
		cmd := exec.Command("git", append([]string{"-c", "user.name=t", "-c", "user.email=t@example.com"}, args...)...)
		cmd.Dir = dir
		if out, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v\n%s", args, err, out)
		}
	}
	git(root, "init", "--quiet", "-b", "main")
	write(t, filepath.Join(root, "a.go"))
	write(t, filepath.Join(root, "docs", "b.md"))
	git(root, "add", ".")
	git(root, "commit", "--quiet", "-m", "init")
	child := filepath.Join(root, ".claude", "worktrees", "c1")
	git(root, "worktree", "add", "--quiet", "-b", "c1", child)
	return fixture{root, filepath.Join(root, "logs", "state"), child}
}

func write(t *testing.T, path string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("x\n"), 0o644); err != nil {
		t.Fatal(err)
	}
}

func (f fixture) note(t *testing.T, tool, cwd string, input, response *pyjson.Object) {
	t.Helper()
	in := hookio.Input{Event: hookio.PostToolUse, ToolName: tool, ToolInput: input, ToolResponse: response, SessionID: "s1", Cwd: cwd}
	Note(f.state, f.root, in, time.Now())
}

func (f fixture) rels(t *testing.T) []string {
	t.Helper()
	var out []string
	for _, item := range Listed(f.state, "s1", f.root, config.DefaultProjects) {
		out = append(out, item.Rel)
	}
	return out
}

func TestToolTargetsAreRecorded(t *testing.T) {
	f := newFixture(t)
	f.note(t, config.ToolRead, f.root, pyjson.NewObject("file_path", filepath.Join(f.root, "a.go")), nil)
	f.note(t, config.ToolEdit, f.child, pyjson.NewObject("file_path", "docs/b.md"), nil)
	if got, want := f.rels(t), []string{"a.go", "docs/b.md"}; !slices.Equal(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

// 子のワークツリーと親のツリーの同じファイルは、ツリーからの相対で 1 件にまとまり、回数の多い順に並ぶ。
func TestSameFileAcrossTreesIsMerged(t *testing.T) {
	f := newFixture(t)
	f.note(t, config.ToolRead, f.root, pyjson.NewObject("file_path", filepath.Join(f.root, "docs", "b.md")), nil)
	f.note(t, config.ToolRead, f.root, pyjson.NewObject("file_path", filepath.Join(f.child, "a.go")), nil)
	f.note(t, config.ToolRead, f.root, pyjson.NewObject("file_path", filepath.Join(f.root, "a.go")), nil)
	listed := Listed(f.state, "s1", f.root, config.DefaultProjects)
	if len(listed) != 2 || listed[0].Rel != "a.go" || listed[0].Count != 2 || listed[0].Tools[config.ToolRead] != 2 {
		t.Fatalf("got %+v", listed)
	}
}

// Bash は引数（`cd` の先からの相対を含む）と、grep -n の形の出力・パスだけの行から、実在するファイルを拾う。
func TestBashArgumentsAndOutputAreRecorded(t *testing.T) {
	f := newFixture(t)
	f.note(t, config.ToolBash, f.root, pyjson.NewObject("command", "cd docs && sed -n 1,20p b.md missing.txt"), nil)
	if got, want := f.rels(t), []string{"docs/b.md"}; !slices.Equal(got, want) {
		t.Fatalf("args: got %v, want %v", got, want)
	}
	f = newFixture(t)
	f.note(t, config.ToolBash, f.root, pyjson.NewObject("command", "grep -rn x ."),
		pyjson.NewObject("stdout", "a.go:1:x\n./docs/b.md\nno such: thing\n"))
	if got, want := f.rels(t), []string{"a.go", "docs/b.md"}; !slices.Equal(got, want) {
		t.Fatalf("output: got %v, want %v", got, want)
	}
}

func TestGrepAndGlobResultsAreRecorded(t *testing.T) {
	f := newFixture(t)
	f.note(t, config.ToolGlob, f.root, pyjson.NewObject("pattern", "**/*.md"),
		pyjson.NewObject("filenames", []any{filepath.Join(f.root, "docs", "b.md")}))
	f.note(t, config.ToolGrep, f.root, pyjson.NewObject("pattern", "x"), pyjson.NewObject("content", "a.go:1:x"))
	if got, want := f.rels(t), []string{"a.go", "docs/b.md"}; !slices.Equal(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

// ワークスペースルートの外と `.git` の中、ほかのセッションの記録は載らない。
func TestOutsideAndGitAndOtherSessionsAreLeftOut(t *testing.T) {
	f := newFixture(t)
	outside := filepath.Join(t.TempDir(), "o.txt")
	write(t, outside)
	f.note(t, config.ToolRead, f.root, pyjson.NewObject("file_path", outside), nil)
	f.note(t, config.ToolRead, f.root, pyjson.NewObject("file_path", filepath.Join(f.root, ".git", "HEAD")), nil)
	if got := Load(f.state, "s1"); len(got) != 0 {
		t.Fatalf("got %+v", got)
	}
	f.note(t, config.ToolRead, f.root, pyjson.NewObject("file_path", filepath.Join(f.root, "a.go")), nil)
	if got := Listed(f.state, "s2", f.root, config.DefaultProjects); len(got) != 0 {
		t.Fatalf("other session: got %+v", got)
	}
}
