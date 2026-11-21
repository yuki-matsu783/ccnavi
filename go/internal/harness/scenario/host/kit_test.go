package host

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/hoststub"
	"ccnavi/internal/harness/run"
)

const (
	realOriginURL = "https://github.com/acme/real.git"
	bodyOriginURL = "https://github.com/acme/body.git"
	fakeProject   = "ns/proj"
	rootPAT       = "root-pat-0001"
	reviewerPAT   = "reviewer-pat-0002"
	dockerBase    = "http://127.0.0.1:8929"
)

// gitFunc は本物の git を run.Command で起こす GitFunc。
func gitFunc(t *testing.T) GitFunc {
	return func(args []string, o GitOpts) GitResult {
		full := append([]string{"-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"}, args...)
		r := run.Command(t, "git", full, run.Opts{Dir: o.Dir, Input: o.Input, Env: o.Env})
		return GitResult{r.Code, r.Stdout, r.Stderr}
	}
}

// workspace はワークスペースルート（本体の代わりの小さなリポジトリ）と、その下の使い捨てのリポジトリ。
type workspace struct {
	root, target, base string
}

func makeRepo(t *testing.T, path, origin string) string {
	t.Helper()
	if err := os.MkdirAll(path, 0o755); err != nil {
		t.Fatal(err)
	}
	run.Git(t, path, "init", "-q", "-b", "main")
	if origin != "" {
		run.Git(t, path, "remote", "add", "origin", origin)
	}
	if err := os.WriteFile(filepath.Join(path, "README.md"), []byte("元の中身\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	run.Git(t, path, "add", ".")
	run.Git(t, path, "commit", "-q", "-m", "base")
	return run.Git(t, path, "rev-parse", "HEAD")
}

func newWorkspace(t *testing.T) workspace {
	t.Helper()
	tmp := base.TempDir(t)
	w := workspace{root: filepath.Join(tmp, "ws")}
	makeRepo(t, w.root, bodyOriginURL)
	w.target = filepath.Join(w.root, "projects", "test-repo")
	w.base = makeRepo(t, w.target, realOriginURL)
	return w
}

// manager は git・スタブの組み立てを本物に向けた Manager。置き場はテストごとの使い捨て。
func manager(t *testing.T) (*Manager, string) {
	t.Helper()
	tmp := base.TempDir(t)
	m := &Manager{
		Git:     gitFunc(t),
		TempDir: tmp,
		Sleep:   func(time.Duration) {},
		BuildStub: func(dir string, curl bool) (hoststub.Stub, error) {
			return hoststub.Build(t, dir, curl), nil
		},
	}
	t.Cleanup(func() {
		m.mu.Lock()
		defer m.mu.Unlock()
		for _, s := range m.servers {
			s.Close()
		}
	})
	return m, tmp
}

// seeded は未解決のスレッドを 1 つ持つ MR が 1 件ある、模擬の初期状態。
func seeded() map[string]any {
	note := map[string]any{
		"id": 101, "body": "ここを直してください", "resolvable": true, "resolved": false,
		"created_at": "2026-10-25T00:00:00Z",
		"author":     map[string]any{"id": 2, "username": "reviewer"},
		"position":   map[string]any{"new_path": "a.txt", "new_line": 3},
	}
	mr := map[string]any{
		"iid": 1, "state": "opened", "source_branch": "i0001", "target_branch": "main", "title": "i0001",
		"web_url":     "http://127.0.0.1/" + fakeProject + "/-/merge_requests/1",
		"discussions": []any{map[string]any{"id": "d101", "individual_note": false, "notes": []any{note}}},
		"reviewers":   []any{}, "notes": []any{}, "created_at": "2026-10-25T00:00:00Z",
	}
	return map[string]any{"project": fakeProject, "seq": 100, "issues": []any{}, "mrs": []any{mr}}
}

func contains(s, sub string) bool { return strings.Contains(s, sub) }

func exeShPath(t *testing.T) string { return exe.ShPath(t) }
