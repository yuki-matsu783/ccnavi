package start

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/shkit"
)

// このファイルは、このパッケージの sh の試験だけが使う道具。sh と git は run.Command・run.Sh・run.Git で起こす。

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

// gitPath は `git rev-parse --git-path name` の絶対パス。
func gitPath(t testing.TB, tree, name string) string {
	t.Helper()
	p := git(t, tree, "rev-parse", "--git-path", name)
	if filepath.IsAbs(p) {
		return p
	}
	return filepath.Join(tree, p)
}

// runScript は script（絶対パス）を sh で 1 回起こす。env は run.Opts.Env の差分。
func runScript(t testing.TB, script, dir string, env map[string]string, args ...string) run.Result {
	t.Helper()
	return run.Sh(t, shkit.Slash(script), args, run.Opts{Dir: dir, Env: env})
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
	return run.Sh(t, shkit.Slash(script), args, run.Opts{Dir: dir, Env: env, Input: input})
}

// touchLater はディレクトリの更新時刻を未来にする。
func touchLater(t testing.TB, path string) {
	t.Helper()
	later := time.Unix(2_000_000_000, 0)
	if err := os.Chtimes(path, later, later); err != nil {
		t.Fatal(err)
	}
}
