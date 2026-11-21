package scenarios

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/hoststub"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/scenario/core"
	"ccnavi/internal/harness/scenario/host"
	"ccnavi/internal/harness/scenario/judge"
	"ccnavi/internal/harness/scenario/runner"
	"ccnavi/internal/harness/scenario/world"
)

const (
	realOrigin = "https://github.com/acme/real.git"
	bodyOrigin = "https://github.com/acme/body.git"
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

// hostWs は本体と対象の origin が（本物の GitHub の形で）違うワークスペース。
func hostWs(t *testing.T) ws { return newWs(t, bodyOrigin, realOrigin) }

func (w ws) git(t *testing.T, args ...string) string { return run.Git(t, w.target, args...) }

func (w ws) dirty(t *testing.T) {
	t.Helper()
	write(t, filepath.Join(w.target, "README.md"), "元の中身\n未コミット\n")
	write(t, filepath.Join(w.target, "untracked.txt"), "x\n")
	w.git(t, "branch", "feat")
	write(t, filepath.Join(w.target, "other.txt"), "o\n")
	w.git(t, "add", "other.txt")
	w.git(t, "commit", "-q", "-m", "ahead")
	write(t, filepath.Join(w.target, "README.md"), "元の中身\n未コミット\n")
}

func (w ws) restored(t *testing.T) bool {
	t.Helper()
	return w.git(t, "rev-parse", "main") == w.base && w.git(t, "branch", "--show-current") == "main" &&
		w.git(t, "status", "--porcelain") == "" && read(t, filepath.Join(w.target, "README.md")) == "元の中身\n"
}

func (w ws) gitDirNames(t *testing.T) map[string]bool {
	t.Helper()
	entries, err := os.ReadDir(filepath.Join(w.target, ".git"))
	if err != nil {
		t.Fatal(err)
	}
	names := map[string]bool{}
	for _, e := range entries {
		names[e.Name()] = true
	}
	return names
}

// scenario はシナリオのファイルを書く。
func (w ws) scenario(t *testing.T, setup map[string]any, steps []map[string]any, name ...string) string {
	t.Helper()
	return writeScenario(t, w.tmp, "UC-99", setup, steps, name...)
}

func writeScenario(t *testing.T, dir, id string, setup map[string]any, steps []map[string]any, name ...string) string {
	t.Helper()
	if steps == nil {
		steps = []map[string]any{{"prompt": "a"}}
	}
	data := map[string]any{"id": id, "title": "t", "steps": steps, "expect": map[string]any{}, "setup": setup}
	text, _ := json.Marshal(data) // JSON は YAML の部分集合
	file := "UC-99-x.yml"
	if len(name) > 0 {
		file = name[0]
	}
	path := filepath.Join(dir, file)
	write(t, path, string(text))
	return path
}

func resetBase(base string) map[string]any { return map[string]any{"base": base} }

// step の差し替え口。
var stepOut = &core.Stream{}

// rig は実行器と、出力・呼ばれた step の記録。
type rig struct {
	r     *runner.Runner
	out   *bytes.Buffer
	mu    sync.Mutex
	calls []runner.StepInput
	// fn は step ごとの動き。nil なら何もしない。
	fn func(in runner.StepInput) (*core.Stream, error)
	// scratch はスタブの置き場（ccnavi-host-*）の親。
	scratch string
}

func newRig(t *testing.T, w ws) *rig {
	t.Helper()
	g := &rig{out: &bytes.Buffer{}, scratch: filepath.Join(w.tmp, "scratch")}
	if err := os.MkdirAll(g.scratch, 0o755); err != nil {
		t.Fatal(err)
	}
	m := &host.Manager{
		Git:     world.Git,
		TempDir: g.scratch,
		Sleep:   func(time.Duration) {},
		BuildStub: func(dir string, curl bool) (hoststub.Stub, error) {
			return hoststub.Build(t, dir, curl), nil
		},
	}
	g.r = &runner.Runner{
		RepoRoot: base.Root(),
		LogsDir:  filepath.Join(w.tmp, "logs"),
		Manager:  m,
		Ccnavi:   func() (string, error) { return exe.Path(t), nil },
		Teststub: func() (string, error) { return exe.TeststubPath(t), nil },
		Getenv:   func(string) string { return "" },
		Out:      g.out,
		Environ:  os.Environ(),
		Step: func(in runner.StepInput) (*core.Stream, error) {
			g.mu.Lock()
			g.calls = append(g.calls, in)
			fn := g.fn
			g.mu.Unlock()
			if fn == nil {
				return stepOut, nil
			}
			return fn(in)
		},
	}
	return g
}

func (g *rig) text() string { return g.out.String() }

func (g *rig) ran() int {
	g.mu.Lock()
	defer g.mu.Unlock()
	return len(g.calls)
}

// runOpts は run の引数（確認済み・出力の置き場つき）。
func (w ws) runOpts(path string) runner.RunOptions {
	return runner.RunOptions{Scenario: path, Confirmed: true, Root: w.root, Out: w.out}
}

func (w ws) state(t *testing.T) *core.State {
	t.Helper()
	st, err := core.LoadState(w.out)
	if err != nil {
		t.Fatal(err)
	}
	return st
}

func contains(s string, subs ...string) bool {
	for _, sub := range subs {
		if !strings.Contains(s, sub) {
			return false
		}
	}
	return true
}

// panics は f が panic したか。
func panics(f func()) (p any) {
	defer func() { p = recover() }()
	f()
	return nil
}

func judgeReport(out string) ([]core.Result, error) { return judge.ReadReport(out) }

func jsonOf(v any) string { data, _ := json.Marshal(v); return string(data) }
