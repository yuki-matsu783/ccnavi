package git

import (
	"os"
	"os/exec"
	"strings"
	"sync"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/shkit"
)

// gitTry は素の git（ラッパーを通さない）を dir で起こし、標準出力と終了コードを返す。環境は run の子と同じ（走った機械の git の設定を締め出してある）。
func gitTry(dir string, args ...string) (string, int) {
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	cmd.Env = run.Environment(run.Opts{})
	out, err := cmd.Output()
	if err == nil {
		return string(out), 0
	}
	if ee, ok := err.(*exec.ExitError); ok {
		return string(out), ee.ExitCode()
	}
	return string(out), -1
}

// git は素の git の標準出力。失敗したらテストを止める。見本のリポジトリを組み、状態を読むために使う。
func git(t testing.TB, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	cmd.Env = run.Environment(run.Opts{})
	out, err := cmd.Output()
	if err != nil {
		stderr := ""
		if ee, ok := err.(*exec.ExitError); ok {
			stderr = string(ee.Stderr)
		}
		t.Fatalf("git %v (%s): %v\n%s", args, dir, err, stderr)
	}
	return string(out)
}

// gitT は git の標準出力の前後の空白を落としたもの。
func gitT(t testing.TB, dir string, args ...string) string {
	t.Helper()
	return strings.TrimSpace(git(t, dir, args...))
}

// gitOK は git が 0 で終わるか。
func gitOK(dir string, args ...string) bool {
	_, code := gitTry(dir, args...)
	return code == 0
}

// initRepo は branch を最初のブランチにした、作者つきの空のリポジトリ。branch が空なら git の既定。
func initRepo(t testing.TB, repo, branch string) {
	t.Helper()
	if err := os.MkdirAll(repo, 0o755); err != nil {
		t.Fatal(err)
	}
	args := []string{"init", "-q"}
	if branch != "" {
		args = append(args, "-b", branch)
	}
	git(t, repo, args...)
	shkit.Identity(t, repo)
}

// seedRepo は initRepo のあと、空のコミットを 1 件積む。
func seedRepo(t testing.TB, repo, branch string) {
	t.Helper()
	initRepo(t, repo, branch)
	git(t, repo, "commit", "-q", "--allow-empty", "-m", "seed")
}

// withPrefix は names のうち prefix で始まるもの。
func withPrefix(all []string, prefix string) []string {
	var out []string
	for _, n := range all {
		if strings.HasPrefix(n, prefix) {
			out = append(out, n)
		}
	}
	return out
}

var (
	wrapperOnce sync.Once
	wrapperPath string
)

// sharedWrapper は exeWrapper を、プロセスで 1 本だけ置いたもの（ワークスペースごとに置かなくてよいとき）。
func sharedWrapper(t testing.TB) string {
	t.Helper()
	wrapperOnce.Do(func() {
		dir, err := os.MkdirTemp(base.TempBase(), "cc-exe-")
		if err != nil {
			t.Fatal(err)
		}
		base.AtExit(dir)
		wrapperPath = shkit.ExeWrapper(t, dir)
	})
	if wrapperPath == "" {
		t.Fatal("実行ファイルのラッパーを置けなかった")
	}
	return wrapperPath
}

// gitWith は env を足した環境で素の git を起こす。
func gitWith(dir string, env map[string]string, args ...string) (string, int) {
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	cmd.Env = run.Environment(run.Opts{Env: env})
	out, err := cmd.Output()
	if ee, ok := err.(*exec.ExitError); ok {
		return string(out), ee.ExitCode()
	}
	if err != nil {
		return string(out), -1
	}
	return string(out), 0
}

// lookGit は PATH の git の場所。
func lookGit() (string, error) { return exec.LookPath("git") }
