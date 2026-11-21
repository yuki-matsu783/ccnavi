// Package setup は、導入・配布の sh（scripts/ccnavi-{setup,install,doctor,package}.sh）と、配る振り分けの sh・ユーザ専用の sh の
// 置き場と起動の形を、外から起こして確かめる（tests/sh/test_setup*.py・test_install_sh.py・test_doctor_sh.py・test_package_sh.py・
// test_launcher.py・test_bin_lookup.py・test_user_scripts_sh.py の移し）。
//
// 道具: kit_test.go（書く・読む・起こす）、iso_test.go（PATH を隔離した偽物つきの環境）、source_test.go（setup が配る偽の配布元）。
package setup

import (
	"bytes"
	"context"
	"errors"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/infra/env"
)

// thisMachine はこの機械の `<os>-<arch>`（sh の uname とは別の読み方。食い違えば落ちる）。
func thisMachine() string { return exe.HostTarget() }

// anotherMachine はどの機械とも一致しない目印。
const anotherMachine = "haiku-riscv64"

// launcherPath は配る振り分けの sh の原本（保護対象の下書きがあれば下書き）。
func launcherPath() string { return filepath.Join(base.ScriptsDir(), "ccnavi-launcher.sh") }

// scriptPath は scripts/ の下の導入の sh。
func scriptPath(name string) string { return filepath.Join(base.Root(), "scripts", name) }

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

func writeExec(t testing.TB, path, text string) string {
	t.Helper()
	write(t, path, text)
	if err := os.Chmod(path, 0o755); err != nil {
		t.Fatal(err)
	}
	return path
}

func mkdir(t testing.TB, path string) string {
	t.Helper()
	if err := os.MkdirAll(path, 0o755); err != nil {
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

func isLink(path string) bool {
	info, err := os.Lstat(path)
	return err == nil && info.Mode()&os.ModeSymlink != 0
}

func canExec(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.Mode()&0o111 != 0
}

func chmod(t testing.TB, path string, mode os.FileMode) {
	t.Helper()
	if err := os.Chmod(path, mode); err != nil {
		t.Fatal(err)
	}
}

func remove(t testing.TB, path string) {
	t.Helper()
	if err := os.RemoveAll(path); err != nil {
		t.Fatal(err)
	}
}

func symlink(t testing.TB, target, link string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(link), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(target, link); err != nil {
		t.Fatal(err)
	}
}

// names はディレクトリの直下の名前（名前順）。
func names(t testing.TB, dir string) []string {
	t.Helper()
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	out := []string{}
	for _, e := range entries {
		out = append(out, e.Name())
	}
	return out
}

// listing は base の下の全ファイルの相対パス（`/` 区切り、名前順）。リンクはファイルとして数える。
func listing(t testing.TB, base string) []string {
	t.Helper()
	found := []string{}
	err := filepath.WalkDir(base, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if !d.IsDir() {
			rel, _ := filepath.Rel(base, path)
			found = append(found, filepath.ToSlash(rel))
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	sort.Strings(found)
	return found
}

// copyTree は src の中身を dst へ写す。リンクはリンク先の中身を写す（symlinks=true ならリンクのまま）。
func copyTree(t testing.TB, src, dst string, keepLinks bool, skip func(rel string) bool) {
	t.Helper()
	err := filepath.WalkDir(src, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(src, path)
		if rel == "." {
			return os.MkdirAll(dst, 0o755)
		}
		if skip != nil && skip(filepath.ToSlash(rel)) {
			if d.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		target := filepath.Join(dst, rel)
		if d.Type()&os.ModeSymlink != 0 && keepLinks {
			to, err := os.Readlink(path)
			if err != nil {
				return err
			}
			return os.Symlink(to, target)
		}
		info, err := os.Stat(path)
		if err != nil {
			return err
		}
		if info.IsDir() {
			return os.MkdirAll(target, 0o755)
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		return os.WriteFile(target, data, info.Mode().Perm())
	})
	if err != nil {
		t.Fatal(err)
	}
}

func copyFile(t testing.TB, from, to string, mode os.FileMode) {
	t.Helper()
	write(t, to, read(t, from))
	chmod(t, to, mode)
}

// snapshot はディレクトリの中身・権限・リンク先。配布元が変わっていないことを比べるのに使う。
func snapshot(t testing.TB, root string) map[string]string {
	t.Helper()
	seen := map[string]string{}
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(root, path)
		if rel == "." {
			return nil
		}
		info, err := os.Lstat(path)
		if err != nil {
			return err
		}
		entry := info.Mode().String()
		switch {
		case info.Mode()&os.ModeSymlink != 0:
			to, _ := os.Readlink(path)
			entry += " -> " + to
		case info.Mode().IsRegular():
			data, err := os.ReadFile(path)
			if err != nil {
				return err
			}
			entry += " " + string(data)
		}
		seen[rel] = entry
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return seen
}

func sameSnapshot(a, b map[string]string) bool {
	if len(a) != len(b) {
		return false
	}
	for k, v := range a {
		if b[k] != v {
			return false
		}
	}
	return true
}

func lf(s string) string { return strings.NewReplacer("\r\n", "\n", "\r", "\n").Replace(s) }

// pathWith は dirs を、いまのプロセスの PATH の前に並べた値。
func pathWith(dirs ...string) string {
	return strings.Join(append(append([]string{}, dirs...), os.Getenv(env.Path)), string(os.PathListSeparator))
}

func gitInit(t testing.TB, dir string) {
	t.Helper()
	mkdir(t, dir)
	r := run.Command(t, "git", []string{"init", "-q", dir}, run.Opts{})
	if r.Code != 0 {
		t.Fatalf("git init: %s", r.Stderr)
	}
}

// haveCommands は commands が全部あるかを見る。無ければ skip。
func haveCommands(t testing.TB, commands ...string) {
	t.Helper()
	for _, c := range commands {
		if _, err := exec.LookPath(c); err != nil {
			t.Skipf("%s が無い", c)
		}
	}
}

func notWindows(t testing.TB) {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("POSIX の話（実行の許し・疑似ターミナル・偽の uname を PATH で差し替える）")
	}
}

// in は text が全部の語を含むことを見る。
func in(t testing.TB, text string, words ...string) {
	t.Helper()
	for _, w := range words {
		if !strings.Contains(text, w) {
			t.Errorf("%q が出ていない:\n%s", w, text)
		}
	}
}

// notIn は text がどの語も含まないことを見る。
func notIn(t testing.TB, text string, words ...string) {
	t.Helper()
	for _, w := range words {
		if strings.Contains(text, w) {
			t.Errorf("%q が出ている:\n%s", w, text)
		}
	}
}

// code は終了コードを見る。違えば出力を添えて落とす。
func code(t testing.TB, r run.Result, want int) {
	t.Helper()
	if r.Code != want {
		t.Fatalf("code = %d, want %d\nstdout:\n%s\nstderr:\n%s", r.Code, want, r.Stdout, r.Stderr)
	}
}

func eq[T comparable](t testing.TB, what string, got, want T) {
	t.Helper()
	if got != want {
		t.Errorf("%s = %v, want %v", what, got, want)
	}
}

// countNext は「次: 」で始まる行の数。
func countNext(text string) int {
	n := 0
	for _, line := range strings.Split(text, "\n") {
		if strings.HasPrefix(line, "次: ") {
			n++
		}
	}
	return n
}

// lastLine は最後の行（末尾の改行は数えない）。
func lastLine(text string) string {
	lines := strings.Split(strings.TrimRight(text, "\n"), "\n")
	return lines[len(lines)-1]
}

func shellQuote(s string) string { return "'" + strings.ReplaceAll(s, "'", `'"'"'`) + "'" }

func withHome(home string, extra map[string]string) map[string]string {
	out := map[string]string{env.Home: home}
	for k, v := range extra {
		out[k] = v
	}
	return out
}

func runOpts(extra map[string]string) run.Opts { return run.Opts{Env: extra} }

func lookPath(t testing.TB, name string) string {
	t.Helper()
	p, err := exec.LookPath(name)
	if err != nil {
		t.Skipf("%s が無い", name)
	}
	return p
}

// runCmdArgs は script を `sh script args…` で起こす。
func runCmdArgs(t testing.TB, script string, args ...string) run.Result {
	t.Helper()
	return run.Command(t, "sh", append([]string{script}, args...), run.Opts{})
}

// runTTY は argv を疑似ターミナルの中で起こし、answer を入力する。疑似ターミナルは `script` で与える（Linux は `script -qec`、
// macOS は `script -q /dev/null`）。無い環境と Windows は skip。出力は標準出力と標準エラーが混ざるので、両方に同じ文字列を入れて返す。
func runTTY(t testing.TB, argv []string, o run.Opts, answer string) run.Result {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("疑似ターミナルは POSIX の話")
	}
	script, err := exec.LookPath("script")
	if err != nil {
		t.Skip("script（util-linux・bsdutils）が無いので --bin user の TTY の試験を飛ばす")
	}
	// script は $SHELL でコマンドを起こす。試したい SHELL（存在しない fish など）は、script の外ではなく中の環境にだけ渡す。
	if shell, ok := o.Env["SHELL"]; ok {
		inner := map[string]string{}
		for k, v := range o.Env {
			inner[k] = v
		}
		inner["SHELL"] = "/bin/sh"
		o.Env = inner
		argv = append([]string{"env", "SHELL=" + shell}, argv...)
	}
	args := []string{"-qec", shellJoin(argv), "/dev/null"}
	if runtime.GOOS == "darwin" {
		args = append([]string{"-q", "/dev/null"}, argv...)
	}
	ctx, cancel := context.WithTimeout(context.Background(), base.ExecTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, script, args...)
	cmd.Env = run.Environment(o)
	cmd.Dir = o.Dir
	cmd.WaitDelay = 5 * time.Second
	cmd.Stdin = strings.NewReader(answer)
	var out bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &out
	err = cmd.Run()
	var exit *exec.ExitError
	if err != nil && !errors.As(err, &exit) {
		t.Fatalf("script を起こせない: %v", err)
	}
	if ctx.Err() != nil {
		t.Fatal("ターミナルの入力を待ち続けた")
	}
	text := lf(out.String())
	return run.Result{Code: cmd.ProcessState.ExitCode(), Stdout: text, Stderr: text}
}

func shellJoin(argv []string) string {
	quoted := make([]string, len(argv))
	for i, a := range argv {
		quoted[i] = shellQuote(a)
	}
	return strings.Join(quoted, " ")
}
