package world

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

func write(t *testing.T, path, text string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
}

func read(t *testing.T, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

func exists(path string) bool { _, err := os.Lstat(path); return err == nil }

// makeRepo は README.md と .gitignore を持つ最初のコミットを作り、その SHA を返す。
func makeRepo(t *testing.T, path, origin string) string {
	t.Helper()
	if err := os.MkdirAll(path, 0o755); err != nil {
		t.Fatal(err)
	}
	run.Git(t, path, "init", "-q", "-b", "main")
	if origin != "" {
		run.Git(t, path, "remote", "add", "origin", origin)
	}
	write(t, filepath.Join(path, "README.md"), "元の中身\n")
	write(t, filepath.Join(path, ".gitignore"), "ignored.log\n")
	run.Git(t, path, "add", ".")
	run.Git(t, path, "commit", "-q", "-m", "base")
	return run.Git(t, path, "rev-parse", "HEAD")
}

// ws はワークスペースルート（本体の代わりの小さなリポジトリ）と、その下の使い捨てのリポジトリ。
type ws struct {
	tmp, root, target, base, out string
}

func newWs(t *testing.T, originRoot, originTarget string) ws {
	t.Helper()
	tmp := base.TempDir(t)
	w := ws{tmp: tmp, root: filepath.Join(tmp, "ws"), out: filepath.Join(tmp, "out")}
	makeRepo(t, w.root, originRoot)
	w.target = filepath.Join(w.root, "projects", "test-repo")
	w.base = makeRepo(t, w.target, originTarget)
	return w
}

// dirty は回したあとの、戻したい散らかり方を一通り作る。
func (w ws) dirty(t *testing.T) {
	t.Helper()
	g := func(args ...string) { run.Git(t, w.target, args...) }
	write(t, filepath.Join(w.target, "README.md"), "元の中身\n追記\n")
	write(t, filepath.Join(w.target, "untracked.txt"), "x\n")
	write(t, filepath.Join(w.target, "ignored.log"), "残る\n")
	g("branch", "feat")
	g("tag", "v1")
	write(t, filepath.Join(w.target, "other.txt"), "o\n")
	g("add", "other.txt")
	g("commit", "-q", "-m", "ahead")
	write(t, filepath.Join(w.target, "README.md"), "元の中身\nstash\n")
	g("stash")
	g("worktree", "add", "-q", filepath.Join(w.root, "projects", "wt"), "-b", "wtbranch")
	g("update-ref", "refs/remotes/origin/keep", "HEAD")
	write(t, filepath.Join(w.target, "README.md"), "元の中身\n未コミット\n")
}

func lines(s string) []string {
	if strings.TrimSpace(s) == "" {
		return nil
	}
	return strings.Split(strings.TrimSpace(s), "\n")
}
