package base

import (
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/ospath"
)

func TestMain(m *testing.M) { Main(m) }

// 走った機械の git の設定を締め出し、既定のブランチ名とグローバルの無視ファイルを固定する。
func TestGitConfigIsBlocked(t *testing.T) {
	t.Parallel()
	dir := TempDir(t)
	get := func(key string) string {
		out, err := exec.Command("git", "-C", dir, "config", "--get", key).Output()
		if err != nil {
			t.Fatalf("git config --get %s: %v", key, err)
		}
		return strings.TrimSpace(string(out))
	}
	if got := get("init.defaultBranch"); got != "master" {
		t.Errorf("init.defaultBranch = %q", got)
	}
	excludes := get("core.excludesFile")
	if info, err := os.Stat(excludes); err != nil || info.Size() != 0 {
		t.Errorf("core.excludesFile = %q は空のファイルのはず（%v）", excludes, err)
	}
	if GitEnv()[env.GitConfigGlobal] == "" || os.Getenv(env.GitConfigNoSystem) != "1" {
		t.Error("git の設定の締め出しが環境に無い")
	}
}

// FAKE_* は親の環境から外れる（子のスタブの設定に混ざらない）。
func TestFakeVariablesOfTheParentAreScrubbed(t *testing.T) {
	t.Parallel()
	for _, name := range []string{"FAKE_X", "FAKE_TARGET_COMMON", "CCNAVI_X", "GIT_DIR"} {
		if !scrubbed(fold(name)) {
			t.Errorf("%s が外れない", name)
		}
	}
	if scrubbed(fold("PATH")) {
		t.Error("PATH は外さない")
	}
}

// PATH の ccnavi があるディレクトリは、ccnavi だけを除いた置き場に替わる。ccnavi の無いディレクトリはそのまま。
func TestDropCcnaviFromPath(t *testing.T) {
	t.Parallel()
	if !CanSymlink() {
		t.Skip("シンボリックリンクを作れない")
	}
	with, without := TempDir(t), TempDir(t)
	for _, name := range []string{"CCNAVI", "other"} { // 大文字小文字は区別しない
		if err := os.WriteFile(filepath.Join(with, name), nil, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	path, err := dropCcnaviFromPath(strings.Join([]string{with, without}, string(os.PathListSeparator)))
	if err != nil {
		t.Fatal(err)
	}
	places := filepath.SplitList(path)
	if len(places) != 2 || places[1] != without || places[0] == with {
		t.Fatalf("PATH = %v", places)
	}
	if _, err := os.Lstat(filepath.Join(places[0], "other")); err != nil {
		t.Errorf("ccnavi 以外が残っていない: %v", err)
	}
	if _, err := os.Lstat(filepath.Join(places[0], "CCNAVI")); err == nil {
		t.Error("ccnavi が残っている")
	}
}

func TestScriptCommandQuotesOnlyWhenNeeded(t *testing.T) {
	t.Parallel()
	plain := TempDir(t)
	if got, want := ScriptCommand(plain, "a.sh"), "sh "+plain+"/.ccnavi/scripts/a.sh"; got != want {
		t.Errorf("ScriptCommand = %q, want %q", got, want)
	}
	cases := map[string]string{
		"/a/b":       "/a/b",
		"/a b/c":     `"/a b/c"`,
		`/a "b"/c`:   `'/a "b"/c'`,
		"/it's/here": `"/it's/here"`,
	}
	for in, want := range cases {
		if got := ShellPath(in); got != want {
			t.Errorf("ShellPath(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestTempDirIsARealPath(t *testing.T) {
	t.Parallel()
	dir := TempDir(t)
	if real, err := filepath.EvalSymlinks(dir); err != nil || real != dir {
		t.Errorf("TempDir = %q, 実パス = %q (%v)", dir, real, err)
	}
}

// macOS の一時置き場（/var → /private/var）が実パスで見える。
func TestTempDirEnvironmentIsARealPath(t *testing.T) {
	t.Parallel()
	if os.TempDir() != TempBase() && !ospath.Windows {
		t.Errorf("os.TempDir() = %q, TempBase() = %q", os.TempDir(), TempBase())
	}
}

// go test は、更新時刻が 2 秒より新しいモジュールの中のファイルを開いたテストの結果を保存しない。
// Uncache（目印を未来の時刻で書き、相対パスで開く）はこれに頼るので、Go を上げたときに 2 回目に (cached) が出ないことを見張る。
func TestGoDoesNotCacheATestThatOpensAFutureFile(t *testing.T) {
	t.Parallel()
	mod := TempDir(t)
	files := map[string]string{
		"go.mod": "module probe\n\ngo 1.20\n",
		"probe_test.go": `package probe

import (
	"os"
	"testing"
)

func TestOpen(t *testing.T) {
	f, err := os.Open("` + uncacheMark + `")
	if err != nil {
		t.Fatal(err)
	}
	f.Close()
}
`,
	}
	for name, text := range files {
		if err := os.WriteFile(filepath.Join(mod, name), []byte(text), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	if err := markFor(mod); err != nil {
		t.Fatal(err)
	}
	goTest := func() string {
		cmd := exec.Command("go", "test", "./...")
		cmd.Dir = mod
		cmd.Env = append(os.Environ(), "GOFLAGS=", "GOTOOLCHAIN=local", "GOWORK=off")
		out, err := cmd.CombinedOutput()
		if err != nil {
			t.Fatalf("go test: %v\n%s", err, out)
		}
		return string(out)
	}
	goTest()
	if out := goTest(); strings.Contains(out, "(cached)") {
		t.Errorf("2 回目が保存された結果で返った。Uncache の仕掛けが効いていない:\n%s", out)
	}
}

// Uncache が開く目印の相対パスは、このパッケージの作業ディレクトリから実在する。壊れると Uncache は開けずに黙って返り、
// go test が保存された結果を返す（偽の合格）。
func TestUncacheMarkPathExists(t *testing.T) {
	t.Parallel()
	if _, err := os.Stat(relMark); err != nil {
		t.Errorf("目印の相対パス %q が開けない: %v", relMark, err)
	}
}

// Final は、下書きがあれば .claude を handoff-.claude に読み替えた下書きを、無ければ本物を指す。
func TestFinalPointsToTheDraftOnlyWhenItExists(t *testing.T) {
	t.Parallel()
	name := "final-test-" + strconv.Itoa(os.Getpid()) + ".sh"
	rel := ".claude/hooks/" + name
	if got, want := Final(rel), filepath.Join(Root(), ".claude", "hooks", name); got != want {
		t.Errorf("下書きが無いとき: %q, 期待 %q", got, want)
	}
	draft := filepath.Join(Root(), "wip", "handoff", "handoff-.claude", "hooks", name)
	if err := os.MkdirAll(filepath.Dir(draft), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(draft, []byte("x\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Remove(draft) })
	if got := Final(rel); got != draft {
		t.Errorf("下書きがあるとき: %q, 期待 %q", got, draft)
	}
	if got, want := Final(".ccnavi/x/"+name), filepath.Join(Root(), ".ccnavi", "x", name); got != want {
		t.Errorf("表に無い階層は変えない: %q, 期待 %q", got, want)
	}
}
