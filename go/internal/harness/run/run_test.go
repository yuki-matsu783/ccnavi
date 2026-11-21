package run

import (
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/infra/env"
)

// Main が外す変数を、Main の前に入れておく（外れていることを見るため）。X_KEEP は外れない。
func TestMain(m *testing.M) {
	for name, value := range map[string]string{
		env.Prefix + "RUN_TEST_LEAK": "1",
		env.ClaudeProjectDir:         "/dev/null/leak",
		env.ClaudeSessionID:          "/dev/null/leak",
		env.GitDir:                   "/dev/null/leak",
		env.GitHubToken:              "/dev/null/leak",
		env.GitLabToken:              "/dev/null/leak",
		"X_KEEP":                     "1",
	} {
		if err := os.Setenv(name, value); err != nil {
			panic(err)
		}
	}
	base.Main(m)
}

func printEnvScript(t *testing.T) string {
	t.Helper()
	path := filepath.Join(base.TempDir(t), "env.sh")
	if err := os.WriteFile(path, []byte("env\nprintf 'a\\r\\nb\\r\\nc\\rd\\r\\n'\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	return path
}

func lines(r Result) map[string]bool {
	out := map[string]bool{}
	for _, l := range strings.Split(r.Stdout, "\n") {
		out[l] = true
	}
	return out
}

// 子に見える環境は、プロセスの環境（Main が外した後）+ 既定（git の締め出しと学びの停止）+ Env の差分 - Unset。
func TestChildEnvironmentIsTheProcessEnvironmentPlusTheDiff(t *testing.T) {
	t.Parallel()
	script := printEnvScript(t)
	got := lines(Sh(t, script, nil))
	for _, want := range []string{"X_KEEP=1", env.GitConfigGlobal + "=" + base.GitEnv()[env.GitConfigGlobal], env.Learning + "=disable"} {
		if !got[want] {
			t.Errorf("%q が見えない:\n%v", want, got)
		}
	}
	for line := range got {
		for _, leaked := range []string{env.Prefix + "RUN_TEST_LEAK=", env.ClaudeProjectDir + "=", env.ClaudeSessionID + "=", env.GitDir + "=", env.GitHubToken + "=", env.GitLabToken + "="} {
			if strings.HasPrefix(line, leaked) {
				t.Errorf("Main が外すはずの変数が見える: %s", line)
			}
		}
	}
	got = lines(Sh(t, script, nil, Opts{Env: map[string]string{"X_GIVEN": "1", env.Learning: "enable"}, Unset: []string{"X_KEEP"}}))
	if !got["X_GIVEN=1"] || !got[env.Learning+"=enable"] {
		t.Errorf("渡した差分が見えない（呼び手の値は既定に勝つ）:\n%v", got)
	}
	if got["X_KEEP=1"] {
		t.Error("Unset した変数が見える")
	}
	if !got[env.GitConfigGlobal+"="+base.GitEnv()[env.GitConfigGlobal]] {
		t.Error("空でない差分でも git の締め出しは残る")
	}
}

// 名前の大文字小文字を区別しない環境では、同じ名前の変数は 1 つだけ残り、あとの層が勝つ。並びは決まっている。
func TestComposeFoldsCaseWhenCaseless(t *testing.T) {
	t.Parallel()
	process := []string{"Path=/a", "B=1", "=C:=C:"}
	defaults := map[string]string{"PATH": "/b"}
	add := map[string]string{"path": "/c", "D": "2"}
	if got, want := compose(process, defaults, []string{"b"}, add, true), []string{"D=2", "path=/c"}; !slices.Equal(got, want) {
		t.Errorf("caseless: %v, want %v", got, want)
	}
	if got, want := compose(process, defaults, []string{"B"}, add, false), []string{"D=2", "PATH=/b", "Path=/a", "path=/c"}; !slices.Equal(got, want) {
		t.Errorf("caseful: %v, want %v", got, want)
	}
}

// 出力の CRLF は LF に揃う。
func TestOutputIsLFNormalized(t *testing.T) {
	t.Parallel()
	r := Sh(t, printEnvScript(t), nil)
	if strings.Contains(r.Stdout, "\r") || !strings.HasSuffix(r.Stdout, "a\nb\nc\nd\n") {
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

// 同じ空の木へ並行に起こしても、文面は 1 回だけ置かれ、exclude の行も 1 行。
func TestMessagesAreCopiedOnceUnderConcurrentStarts(t *testing.T) {
	t.Parallel()
	root := base.TempDir(t)
	if err := os.MkdirAll(filepath.Join(root, ".git"), 0o755); err != nil {
		t.Fatal(err)
	}
	var wg sync.WaitGroup
	for range 8 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			putMessagesWhenAbsent(t, root)
		}()
	}
	wg.Wait()
	exclude, err := os.ReadFile(filepath.Join(root, ".git", "info", "exclude"))
	if err != nil || strings.Count(string(exclude), "/.ccnavi/messages/") != 1 {
		t.Errorf("exclude = %q (%v)", exclude, err)
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

// fatalTB は Fatalf（Fatal）を記録して、そこで止める TB。Ccnavi が検査で落とすのを、テストの外から見るために使う。
type fatalTB struct {
	testing.TB
	msg string
}

type fatalStop struct{}

func (f *fatalTB) Fatalf(format string, args ...any) {
	f.msg = fmt.Sprintf(format, args...)
	panic(fatalStop{})
}

func (f *fatalTB) Fatal(args ...any) {
	f.msg = fmt.Sprint(args...)
	panic(fatalStop{})
}

// 使い捨て置き場の上に `.claude` を持つ祖先（利用者のホーム）がある機械で、--root なしの起動が祖先の根へ文面を書かない。
func TestWithoutRootOutsideTheTempBaseIsRefusedAndWritesNothing(t *testing.T) {
	home := base.TempDir(t)
	if err := os.MkdirAll(filepath.Join(home, ".claude"), 0o755); err != nil {
		t.Fatal(err)
	}
	tmp := filepath.Join(home, "tmp")
	if err := os.MkdirAll(tmp, 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("TMPDIR", tmp)
	t.Setenv("TEMP", tmp)
	t.Setenv("TMP", tmp)
	dir := base.TempDir(t)
	f := &fatalTB{TB: t}
	func() {
		defer func() {
			if r := recover(); r != nil {
				if _, ok := r.(fatalStop); !ok {
					panic(r)
				}
			}
		}()
		Ccnavi(f, []string{"--version"}, Opts{Dir: dir})
	}()
	if f.msg != "" && !strings.Contains(f.msg, "使い捨て置き場の外") {
		t.Fatalf("別の理由で落ちた: %s", f.msg)
	}
	if _, err := os.Lstat(filepath.Join(home, ".ccnavi")); err == nil {
		t.Errorf("祖先の根 %s に .ccnavi を書いた", home)
	}
	if f.msg == "" {
		t.Error("根が使い捨て置き場の外なのに落ちなかった")
	}
}
