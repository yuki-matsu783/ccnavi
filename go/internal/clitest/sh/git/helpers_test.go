package git

import (
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/run"
)

// need は、無い道具があればテストを飛ばす。
func need(t testing.TB, tools ...string) {
	t.Helper()
	for _, tool := range tools {
		if _, err := exec.LookPath(tool); err != nil {
			t.Skipf("%s が無い", tool)
		}
	}
}

// write は親ディレクトリごと作って、LF のまま書く。
func write(t testing.TB, path, text string) string {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

// writeExec は write して実行できるようにする。
func writeExec(t testing.TB, path, text string) string {
	t.Helper()
	write(t, path, text)
	if err := os.Chmod(path, 0o755); err != nil {
		t.Fatal(err)
	}
	return path
}

func read(t testing.TB, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

func exists(path string) bool {
	_, err := os.Lstat(path)
	return err == nil
}

func isFile(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.Mode().IsRegular()
}

func isDir(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.IsDir()
}

func remove(t testing.TB, path string) {
	t.Helper()
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
}

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

// identity は、コミットできるように作者を決める（走った機械の git の設定は締め出してあるので、リポジトリごとに要る）。
func identity(t testing.TB, repo string) {
	t.Helper()
	for _, kv := range [][2]string{{"user.email", "t@example.invalid"}, {"user.name", "t"}, {"commit.gpgsign", "false"}} {
		git(t, repo, "config", kv[0], kv[1])
	}
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
	identity(t, repo)
}

// seedRepo は initRepo のあと、空のコミットを 1 件積む。
func seedRepo(t testing.TB, repo, branch string) {
	t.Helper()
	initRepo(t, repo, branch)
	git(t, repo, "commit", "-q", "--allow-empty", "-m", "seed")
}

// copyScripts は ws/.ccnavi/scripts/ へ、共通部（base.CommonSh）と names をコピーして、その置き場を返す。
func copyScripts(t testing.TB, ws string, names ...string) string {
	t.Helper()
	dir := filepath.Join(ws, ".ccnavi", "scripts")
	copyInto(t, dir, names...)
	return dir
}

// copyInto は dir へ、共通部（base.CommonSh）と names をコピーする。
func copyInto(t testing.TB, dir string, names ...string) {
	t.Helper()
	src := base.ScriptsDir()
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	for _, name := range append(append([]string{}, names...), base.CommonSh(t, src)...) {
		data, err := os.ReadFile(filepath.Join(src, name))
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, name), data, 0o755); err != nil {
			t.Fatal(err)
		}
	}
}

// exeWrapper は、ccnavi の実行ファイルを起こすだけの sh を dir/ccnavi に置いて、そのパスを返す（CCNAVI_ENTRY_PATH に渡す）。
func exeWrapper(t testing.TB, dir string) string {
	t.Helper()
	return writeExec(t, filepath.Join(dir, "ccnavi"), "#!/bin/sh\nexec '"+exe.ShPath(t)+"' \"$@\"\n")
}

// slash は sh に渡すパスの区切りを / にそろえる。
func slash(path string) string { return filepath.ToSlash(path) }

// names はディレクトリの直下の名前（辞書順）。
func names(t testing.TB, dir string) []string {
	t.Helper()
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	out := make([]string, 0, len(entries))
	for _, e := range entries {
		out = append(out, e.Name())
	}
	sort.Strings(out)
	return out
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
		wrapperPath = exeWrapper(t, dir)
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
	return string(out), 0
}

// lookGit は PATH の git の場所。
func lookGit() (string, error) { return exec.LookPath("git") }
