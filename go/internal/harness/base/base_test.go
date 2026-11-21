package base

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/env"
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

// PATH の ccnavi があるディレクトリは、ccnavi だけを除いた置き場に替わる。ccnavi の無いディレクトリはそのまま。
// プロセスの環境を書き換えるので、並行にしない（t.Setenv が止める）。
func TestDropCcnaviFromPath(t *testing.T) {
	if !CanSymlink() {
		t.Skip("シンボリックリンクを作れない")
	}
	with, without := TempDir(t), TempDir(t)
	for _, name := range []string{"CCNAVI", "other"} { // 大文字小文字は区別しない
		if err := os.WriteFile(filepath.Join(with, name), nil, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv(env.Path, strings.Join([]string{with, without}, string(os.PathListSeparator)))
	if err := dropCcnaviFromPath(); err != nil {
		t.Fatal(err)
	}
	places := filepath.SplitList(os.Getenv(env.Path))
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
