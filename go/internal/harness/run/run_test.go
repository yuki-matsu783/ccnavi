package run

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/infra/env"
)

func TestMain(m *testing.M) { base.Main(m) }

func printEnvScript(t *testing.T) string {
	t.Helper()
	path := filepath.Join(base.TempDir(t), "env.sh")
	if err := os.WriteFile(path, []byte("env\nprintf 'a\\r\\nb\\r\\n'\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	return path
}

// Env を渡さなければプロセスの環境が、渡せばそのマップ（と git の締め出し・学びの停止）だけが見える。
func TestEnvIsExactlyWhatWasGiven(t *testing.T) {
	t.Parallel()
	script := printEnvScript(t)
	r := Sh(t, script, nil)
	if !strings.Contains(r.Stdout, env.GitConfigGlobal+"=") || !strings.Contains(r.Stdout, "PATH=") {
		t.Errorf("継ぐ環境に git の締め出しか PATH が無い:\n%s", r.Stdout)
	}
	r = Sh(t, script, nil, Opts{Env: map[string]string{"X_GIVEN": "1", env.Learning: "enable"}})
	lines := strings.Split(r.Stdout, "\n")
	has := func(line string) bool {
		for _, l := range lines {
			if l == line {
				return true
			}
		}
		return false
	}
	if !has("X_GIVEN=1") || !has(env.GitConfigGlobal+"="+base.GitEnv()[env.GitConfigGlobal]) {
		t.Errorf("渡した環境か git の締め出しが無い:\n%s", r.Stdout)
	}
	if !has(env.Learning + "=enable") {
		t.Errorf("呼び手の値が既定（disable）に負けた:\n%s", r.Stdout)
	}
	if strings.Contains(r.Stdout, "HOME=") && os.Getenv("HOME") != "" && !has("HOME="+os.Getenv("HOME")) {
		t.Error("渡していない変数が見えている")
	}
	if r := Sh(t, script, nil, Opts{Env: map[string]string{}}); !strings.Contains(r.Stdout, env.Learning+"=disable") {
		t.Errorf("空のマップでも学びの停止は足す:\n%s", r.Stdout)
	}
}

// 出力の CRLF は LF に揃う。
func TestOutputIsLFNormalized(t *testing.T) {
	t.Parallel()
	r := Sh(t, printEnvScript(t), nil)
	if strings.Contains(r.Stdout, "\r") || !strings.HasSuffix(r.Stdout, "a\nb\n") {
		t.Errorf("stdout の末尾 = %q", r.Stdout[max(0, len(r.Stdout)-10):])
	}
}

// 文面ファイルは、木に無ければ写して git の見えない所に置く。リポジトリの中の木には写さない。
func TestMessagesAreCopiedOnlyWhenAbsent(t *testing.T) {
	t.Parallel()
	root := base.TempDir(t)
	if err := os.MkdirAll(filepath.Join(root, ".git"), 0o755); err != nil {
		t.Fatal(err)
	}
	putMessagesWhenAbsent(t, root)
	if _, err := os.Stat(filepath.Join(root, ".ccnavi", "messages")); err != nil {
		t.Errorf("文面が写っていない: %v", err)
	}
	exclude, err := os.ReadFile(filepath.Join(root, ".git", "info", "exclude"))
	if err != nil || !strings.Contains(string(exclude), "/.ccnavi/messages/") {
		t.Errorf("exclude = %q (%v)", exclude, err)
	}
	inside := filepath.Join(base.Root(), "tests", "fixtures")
	putMessagesWhenAbsent(t, inside)
	if _, err := os.Stat(filepath.Join(inside, ".ccnavi")); err == nil {
		t.Error("リポジトリの中に文面を写した")
	}
}

func TestRootOfFollowsTheExecutablesOrder(t *testing.T) {
	t.Parallel()
	ws := base.TempDir(t)
	if err := os.MkdirAll(filepath.Join(ws, ".claude", "worktrees", "w", ".claude"), 0o755); err != nil {
		t.Fatal(err)
	}
	cases := []struct {
		name string
		args []string
		env  []string
		dir  string
		want string
	}{
		{"option", []string{"--root", "/x"}, nil, ws, "/x"},
		{"option=", []string{"--root=/y"}, nil, ws, "/y"},
		{"project dir", nil, []string{env.ClaudeProjectDir + "=/z"}, ws, "/z"},
		{"walk up to .claude", nil, nil, filepath.Join(ws, "sub"), ""},
		// ワークツリーの中は、ワークツリーでなく、その外の .claude を持つ所をルートとする。
		{"worktree is skipped", nil, nil, filepath.Join(ws, ".claude", "worktrees", "w"), ws},
	}
	if err := os.MkdirAll(filepath.Join(ws, "sub"), 0o755); err != nil {
		t.Fatal(err)
	}
	cases[3].want = ws
	for _, c := range cases {
		if got := rootOf(c.args, c.env, c.dir); got != c.want {
			t.Errorf("%s: rootOf = %q, want %q", c.name, got, c.want)
		}
	}
}
