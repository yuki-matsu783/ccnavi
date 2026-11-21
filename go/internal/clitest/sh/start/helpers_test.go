package start

import (
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/run"
)

// このファイルは、このパッケージの sh の試験だけが使う道具。sh と git は run.Command・run.Sh・run.Git で起こす。

// need は、無い道具があればテストを飛ばす。
func need(t testing.TB, tools ...string) {
	t.Helper()
	for _, tool := range tools {
		if _, err := exec.LookPath(tool); err != nil {
			t.Skipf("%s が無い", tool)
		}
	}
}

// write は親ディレクトリごと作って LF のまま書き、パスを返す。
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

// appendTo はファイルの終わりに足す（無ければ作る）。
func appendTo(t testing.TB, path, text string) {
	t.Helper()
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	if _, err := f.WriteString(text); err != nil {
		t.Fatal(err)
	}
}

// exists はリンクの先を辿らずに在るか（Python の os.path.lexists）。
func exists(path string) bool {
	_, err := os.Lstat(path)
	return err == nil
}

func isDir(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.IsDir()
}

func mkdir(t testing.TB, path string) {
	t.Helper()
	if err := os.MkdirAll(path, 0o755); err != nil {
		t.Fatal(err)
	}
}

func remove(t testing.TB, path string) {
	t.Helper()
	if err := os.RemoveAll(path); err != nil {
		t.Fatal(err)
	}
}

// slash は sh に渡すパスの区切りを / にそろえる。
func slash(path string) string { return filepath.ToSlash(path) }

// git は dir で git を起こして標準出力の前後の空白を落として返す。落ちたらテストを止める（run.Git）。
func git(t testing.TB, dir string, args ...string) string {
	t.Helper()
	return run.Git(t, dir, args...)
}

// gitRes は git を起こして結果をそのまま返す（失敗してもテストを止めない）。
func gitRes(t testing.TB, dir string, args ...string) run.Result {
	t.Helper()
	return run.Command(t, "git", args, run.Opts{Dir: dir})
}

// gitOK は git が 0 で終わるか。
func gitOK(t testing.TB, dir string, args ...string) bool {
	t.Helper()
	return gitRes(t, dir, args...).Code == 0
}

// sha は ref の sha。無ければ空。
func sha(t testing.TB, dir, ref string) string {
	t.Helper()
	r := gitRes(t, dir, "rev-parse", "--verify", "-q", ref)
	if r.Code != 0 {
		return ""
	}
	return strings.TrimSpace(r.Stdout)
}

// identity は、コミットできるように作者を決める（走った機械の git の設定は締め出してあるので、リポジトリごとに要る）。
func identity(t testing.TB, repo string) {
	t.Helper()
	for _, kv := range [][2]string{{"user.email", "t@example.invalid"}, {"user.name", "t"}, {"commit.gpgsign", "false"}} {
		git(t, repo, "config", kv[0], kv[1])
	}
}

// gitPath は `git rev-parse --git-path name` の絶対パス。
func gitPath(t testing.TB, tree, name string) string {
	t.Helper()
	p := git(t, tree, "rev-parse", "--git-path", name)
	if filepath.IsAbs(p) {
		return p
	}
	return filepath.Join(tree, p)
}

// copyTree は src を dst へ写す。リンクはリンクのまま、権限は保つ。
func copyTree(t testing.TB, src, dst string) {
	t.Helper()
	err := filepath.WalkDir(src, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(src, path)
		if err != nil {
			return err
		}
		target := filepath.Join(dst, rel)
		info, err := d.Info()
		if err != nil {
			return err
		}
		switch {
		case d.Type()&fs.ModeSymlink != 0:
			link, err := os.Readlink(path)
			if err != nil {
				return err
			}
			return os.Symlink(link, target)
		case d.IsDir():
			return os.MkdirAll(target, info.Mode().Perm()|0o700)
		default:
			data, err := os.ReadFile(path)
			if err != nil {
				return err
			}
			return os.WriteFile(target, data, info.Mode().Perm())
		}
	})
	if err != nil {
		t.Fatal(err)
	}
}

// copyScripts は ws/.ccnavi/scripts/ へ、names と共通部（base.CommonSh）を、できあがりの姿（base.ScriptsDir）から写し、その置き場を返す。
func copyScripts(t testing.TB, ws string, names ...string) string {
	t.Helper()
	return copyScriptsTo(t, filepath.Join(ws, ".ccnavi", "scripts"), names...)
}

// copyScriptsTo は dir へ copyScripts と同じものを写す。
func copyScriptsTo(t testing.TB, dir string, names ...string) string {
	t.Helper()
	src := base.ScriptsDir()
	mkdir(t, dir)
	for _, name := range append(append([]string{}, names...), base.CommonSh(t, src)...) {
		data, err := os.ReadFile(filepath.Join(src, name))
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, name), data, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	return dir
}

// exeWrapper は、ccnavi の実行ファイルを起こすだけの sh を dir/ccnavi に置いて、そのパスを返す（CCNAVI_ENTRY_PATH に渡す）。
func exeWrapper(t testing.TB, dir string) string {
	t.Helper()
	return writeExec(t, filepath.Join(dir, "ccnavi"), "#!/bin/sh\nexec '"+exe.ShPath(t)+"' \"$@\"\n")
}

// runScript は script（絶対パス）を sh で 1 回起こす。env は run.Opts.Env の差分。
func runScript(t testing.TB, script, dir string, env map[string]string, args ...string) run.Result {
	t.Helper()
	return run.Sh(t, slash(script), args, run.Opts{Dir: dir, Env: env})
}

// merge は env の差分を重ねる（あとの値が優先）。
func merge(envs ...map[string]string) map[string]string {
	out := map[string]string{}
	for _, e := range envs {
		for k, v := range e {
			out[k] = v
		}
	}
	return out
}

// out は標準出力と標準エラーをつなげたもの（失敗の表示に使う）。
func out(r run.Result) string { return r.Stdout + r.Stderr }

// code は終了コードが want でなければテストを止める。
func code(t testing.TB, r run.Result, want int) run.Result {
	t.Helper()
	if r.Code != want {
		t.Fatalf("終了コード = %d、want %d\nstdout: %s\nstderr: %s", r.Code, want, r.Stdout, r.Stderr)
	}
	return r
}

// has は s が subs をすべて含むことを確かめる。
func has(t testing.TB, s string, subs ...string) {
	t.Helper()
	for _, sub := range subs {
		if !strings.Contains(s, sub) {
			t.Errorf("%q を含まない: %s", sub, s)
		}
	}
}

// lacks は s が subs のどれも含まないことを確かめる。
func lacks(t testing.TB, s string, subs ...string) {
	t.Helper()
	for _, sub := range subs {
		if strings.Contains(s, sub) {
			t.Errorf("%q を含む: %s", sub, s)
		}
	}
}

func eq[T comparable](t testing.TB, got, want T, what string) {
	t.Helper()
	if got != want {
		t.Errorf("%s = %v、want %v", what, got, want)
	}
}

func truth(t testing.TB, ok bool, what string) {
	t.Helper()
	if !ok {
		t.Error(what)
	}
}

// lastLine は標準出力の最後の行。
func lastLine(s string) string {
	lines := strings.Split(strings.TrimRight(s, "\n"), "\n")
	return lines[len(lines)-1]
}

// exeShPath は sh に書くための ccnavi の実行ファイルのパス。
func exeShPath(t testing.TB) string { return exe.ShPath(t) }

// runWithInput は script を sh で起こし、標準入力を渡す。
func runWithInput(t testing.TB, script, dir, input string, env map[string]string, args ...string) run.Result {
	t.Helper()
	return run.Sh(t, slash(script), args, run.Opts{Dir: dir, Env: env, Input: input})
}

// touchLater はディレクトリの更新時刻を未来にする。
func touchLater(t testing.TB, path string) {
	t.Helper()
	later := time.Unix(2_000_000_000, 0)
	if err := os.Chtimes(path, later, later); err != nil {
		t.Fatal(err)
	}
}
