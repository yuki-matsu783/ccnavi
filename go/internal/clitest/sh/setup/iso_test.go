package setup

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/infra/env"
)

// 導入の sh（ccnavi-doctor.sh・ccnavi-install.sh・ccnavi-package.sh）のテストが使う道具立て。
//
// 道具を隔離する。偽物を PATH の先頭に置くだけでは、後ろの本物が見つかってしまう。テストごとに一時の bin ディレクトリを作り、
// 要る道具だけを本物へのリンクで置き、PATH をそのディレクトリだけにする。glab・jq・curl・git・setup・go は、引数を記録する偽物にできる。
// 記録は `calls.log` に `<道具> <引数>` で 1 行ずつ。
//
// この環境の rm は安全確認のラッパーで、PATH の次の rm を呼ぶ。PATH を bin だけにすると次の rm が無く、ラッパー経由で rm を呼ぶ場面は
// 「rm: command not found」(127) で落ちる（ラッパーは迂回しない）。

// baseTools は導入の sh が使う、ふつうの道具。ここに無いものは PATH に無い。
var baseTools = []string{
	"sh", "sed", "grep", "awk", "cat", "mkdir", "cp", "rm", "tr", "head", "tail",
	"dirname", "basename", "uname", "date", "sleep", "kill", "cut", "ps", "cksum",
	"chmod", "ls", "wc", "find", "sort", "mktemp",
}

type iso struct {
	t                          *testing.T
	tmp, bin, calls, home, ctl string
}

// newIso は隔離した PATH の環境を作る。sh・git・jq・ps が要る（Windows は symlink と POSIX の道具が前提なので飛ばす）。
func newIso(t *testing.T) *iso {
	t.Helper()
	if runtime.GOOS == "windows" || !base.CanSymlink() {
		t.Skip("POSIX の道具を PATH へリンクで置く")
	}
	haveCommands(t, "sh", "git", "jq", "ps")
	e := &iso{t: t, tmp: base.TempDir(t)}
	e.bin = mkdir(t, filepath.Join(e.tmp, "bin"))
	e.calls = filepath.Join(e.tmp, "calls.log")
	e.home = mkdir(t, filepath.Join(e.tmp, "home"))
	e.ctl = mkdir(t, filepath.Join(e.tmp, "ctl"))
	for _, name := range baseTools {
		e.link(name)
	}
	return e
}

func (e *iso) link(name string) {
	real, err := exec.LookPath(name)
	if err != nil {
		return
	}
	dst := filepath.Join(e.bin, name)
	_ = os.Remove(dst)
	if err := os.Symlink(real, dst); err != nil {
		e.t.Fatal(err)
	}
}

func (e *iso) unlink(name string) { _ = os.Remove(filepath.Join(e.bin, name)) }

// fake は引数を calls.log に記録する偽物を置く。body は記録のあとに実行する sh。
func (e *iso) fake(name, body string) {
	e.t.Helper()
	dst := filepath.Join(e.bin, name)
	_ = os.Remove(dst)
	writeExec(e.t, dst, fmt.Sprintf("#!/bin/sh\nprintf '%%s %%s\\n' %s \"$*\" >> \"%s\"\n%s\n", name, e.calls, body))
}

func (e *iso) ctlSet(key, value string) { write(e.t, filepath.Join(e.ctl, key), value) }

func (e *iso) ctlRef(key string) string { return filepath.Join(e.ctl, key) }

// fakeGit はふつうの git に委ねるが、通信するものだけ偽物にする。挙動は ctl の値で決める。
func (e *iso) fakeGit() {
	e.t.Helper()
	leaked := "fatal: https://h/x?private_token=abc12345xyz ghp_abcdefghijkl glrt-abcdefghij12 s3cr3tvalue99 https://u:tok@host/g/p.git/"
	refused := "fatal: unable to access 'https://u:tok@host/g/p.git/': Could not resolve host"
	body := fmt.Sprintf(`case "$*" in
*ls-remote*)
	mode=$(cat "%[1]s" 2>/dev/null || echo ok)
	case "$mode" in
	ok) printf 'abc\tHEAD\n'; exit 0 ;;
	empty) exit 2 ;;
	leak) printf '%%s\n' "%[2]s" >&2; exit 128 ;;
	fail) printf '%%s\n' "%[3]s" >&2; exit 128 ;;
	sleep) sleep "$(cat "%[4]s")" & wait; exit 0 ;;
	esac
	;;
*pull*)
	exit 0
	;;
esac
exec "%[5]s" "$@"
`, e.ctlRef("ls"), leaked, refused, e.ctlRef("ls_sleep"), lookPath(e.t, "git"))
	e.fake("git", body)
	e.ctlSet("ls", "ok")
}

func (e *iso) fakeGlab(credential, apiOK bool) {
	sub := "login"
	if credential {
		sub = "git-credential"
	}
	apiRC := 1
	if apiOK {
		apiRC = 0
	}
	e.fake("glab", fmt.Sprintf(`case "$*" in
"auth --help") printf 'Available commands:\n  %s\n'; exit 0 ;;
"--version") printf 'glab 1.52.0 (2025-01-01)\n'; exit 0 ;;
api*) exit %d ;;
esac
exit 0
`, sub, apiRC))
}

// fakeCurl は curl の応答を ctl の `curl` で切り替える（ok / 401 / 404 / timeout / unreach）。
func (e *iso) fakeCurl(mode string) {
	e.fake("curl", fmt.Sprintf(`mode=$(cat "%s" 2>/dev/null || echo ok)
case "$mode" in
ok) printf '{"id":1}'; exit 0 ;;
401) printf 'curl: (22) The requested URL returned error: 401\n' >&2; exit 22 ;;
404) printf 'curl: (22) The requested URL returned error: 404\n' >&2; exit 22 ;;
timeout) printf 'curl: (28) Operation timed out\n' >&2; exit 28 ;;
unreach) printf 'curl: (7) Failed to connect to host\n' >&2; exit 7 ;;
esac
`, e.ctlRef("curl")))
	e.ctlSet("curl", mode)
}

// env は子の環境の差分（kv は名前と値の並び）。PATH は隔離した bin だけ。Claude Code の中で回しても、CLAUDECODE は外す。
func (e *iso) env(kv ...string) run.Opts {
	m := map[string]string{
		env.Path:                        e.bin,
		env.Home:                        e.home,
		"XDG_CACHE_HOME":                filepath.Join(e.home, ".cache"),
		"CCNAVI_DOCTOR_GLAB_CANDIDATES": "",
		"CCNAVI_DOCTOR_JQ_CANDIDATES":   "",
		"LANG":                          "C.UTF-8",
	}
	for i := 0; i+1 < len(kv); i += 2 {
		m[kv[i]] = kv[i+1]
	}
	return run.Opts{Env: m, Unset: []string{"CLAUDECODE"}}
}

// gitRepo は作業場の git リポジトリを作る（origin があれば付ける）。
func (e *iso) gitRepo(path, origin string) string {
	e.t.Helper()
	gitInit(e.t, path)
	if origin != "" {
		git(e.t, path, "remote", "add", "origin", origin)
	}
	return path
}

// sh は隔離した bin の sh で args を起こす。
func (e *iso) sh(args []string, o run.Opts, dir string) run.Result {
	e.t.Helper()
	o.Dir = dir
	return runCmd(e.t, filepath.Join(e.bin, "sh"), args, o)
}

func (e *iso) callsText() string {
	data, err := os.ReadFile(e.calls)
	if err != nil {
		return ""
	}
	return string(data)
}

// running は needle を含むプロセスの行（ps 自身は除く）。
func running(needle string) []string {
	out, _ := exec.Command("ps", "-eo", "pid=,args=").Output()
	var found []string
	for _, line := range strings.Split(string(out), "\n") {
		if strings.Contains(line, needle) && !strings.Contains(line, "ps -eo") {
			found = append(found, line)
		}
	}
	return found
}

// waitGone は needle のプロセスが消えるのを待つ。残っていれば、その行を返す。
func waitGone(needle string, seconds float64) []string {
	end := time.Now().Add(time.Duration(seconds * float64(time.Second)))
	for time.Now().Before(end) {
		if len(running(needle)) == 0 {
			return nil
		}
		time.Sleep(100 * time.Millisecond)
	}
	return running(needle)
}
