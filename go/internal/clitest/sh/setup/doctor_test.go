package setup

import (
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"reflect"
	"strings"
	"syscall"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/shkit"
)

// scripts/ccnavi-doctor.sh の受入テスト。外から動かし、出力と終了コードだけを読む。道具は隔離した bin だけを PATH にし、glab・curl・git の通信は
// 記録つきの偽物にする。doctor は 1 回の起動が重いので、1 回の起動で読める項目はまとめて確かめる。origin の URL の読み方は
// `tests/fixtures/host-kinds.json` の表（tests/sh/test_host_kinds_sh.py と Go の host_test.go）が確かめるので、ここでは分類ごとの段階だけを見る。

const gitlabOrigin = "https://gitlab.example.com/g/p.git"

type check struct{ status, detail, rest string }

// checks は `check: <名> <状態> <詳細> <残り>` の行を名前で引く。
func checks(out string) map[string]check {
	found := map[string]check{}
	for _, line := range strings.Split(out, "\n") {
		rest, ok := strings.CutPrefix(line, "check: ")
		if !ok {
			continue
		}
		parts := strings.SplitN(rest, " ", 4)
		for len(parts) < 4 {
			parts = append(parts, "")
		}
		found[parts[0]] = check{parts[1], parts[2], parts[3]}
	}
	return found
}

func (c check) is(status, detail string) bool { return c.status == status && c.detail == detail }

func wantCheck(t testing.TB, got map[string]check, name, status, detail string) {
	t.Helper()
	if !got[name].is(status, detail) {
		t.Errorf("check %s = %+v, want %s %s", name, got[name], status, detail)
	}
}

type doctorEnv struct {
	*iso
	ws, src string
}

func newDoctor(t *testing.T) *doctorEnv {
	t.Helper()
	d := &doctorEnv{iso: newIso(t)}
	d.link("jq")
	d.fakeGit()
	d.ws = d.gitRepo(filepath.Join(d.tmp, "ws"), gitlabOrigin)
	d.src = d.makeFrom("", true)
	return d
}

// makeFrom は配布元のふり。target が空ならこの機械。exe が偽なら実行ファイルを置かない。
func (d *doctorEnv) makeFrom(target string, exe bool) string {
	src := filepath.Join(d.tmp, "src")
	if target == "" {
		target = thisMachine()
	}
	copyFile(d.t, filepath.Join(base.ScriptsDir(), "ccnavi-common-host.sh"), filepath.Join(src, ".ccnavi", "scripts", "ccnavi-common-host.sh"), 0o644)
	shkit.Write(d.t, filepath.Join(src, "dist", "ccnavi.target"), target+"\n")
	shkit.Mkdir(d.t, filepath.Join(src, "dist", "ccnavi"))
	if exe {
		shkit.WriteExec(d.t, filepath.Join(src, "dist", "ccnavi", "ccnavi"), "#!/bin/sh\nexit 0\n")
	}
	return src
}

var doctorSh = func() string { return scriptPath("ccnavi-doctor.sh") }

func (d *doctorEnv) doctor(o run.Opts, args ...string) run.Result {
	d.t.Helper()
	return d.sh(append([]string{doctorSh(), "--workspace", d.ws, "--from", d.src}, args...), o, "")
}

func (d *doctorEnv) online(kv ...string) run.Result { return d.doctor(d.env(kv...), "--online") }

func (d *doctorEnv) writeSettings(name, text string) {
	shkit.Write(d.t, filepath.Join(d.ws, ".claude", name), text)
}

func (d *doctorEnv) setOrigin(args ...string) {
	d.t.Helper()
	run.Git(d.t, d.ws, append([]string{"remote"}, args...)...)
}

// ---- 既定の起動（道具は最小）

func TestDoctorDefaultRun(t *testing.T) {
	t.Parallel()
	d := newDoctor(t)
	r := d.doctor(d.env())
	code(t, r, 0)
	lines := strings.Split(strings.TrimRight(r.Stdout, "\n"), "\n")
	for _, line := range lines {
		if strings.HasPrefix(line, "check: ") {
			switch strings.Split(line, " ")[2] {
			case "ok", "warn", "todo", "ng":
			default:
				t.Errorf("状態が決まりの外: %s", line)
			}
		}
	}
	eq(t, "次: の数", countNext(r.Stdout), 1)
	last := lines[len(lines)-1]
	if !strings.HasPrefix(last, "次: sh '") || !strings.Contains(last, "ccnavi-install.sh' '"+d.ws+"' --from '"+d.src+"' --check") {
		t.Errorf("最後の行 = %s", last)
	}
	guide := -1
	n := 0
	for i, line := range lines {
		if strings.HasPrefix(line, "案内: glab が todo") {
			guide, n = i, n+1
		}
	}
	if n != 1 || guide >= len(lines)-1 {
		t.Errorf("案内の行 = %d 本、位置 %d\n%s", n, guide, r.Stdout)
	}
	got := checks(r.Stdout)
	wantCheck(t, got, "machine", "ok", thisMachine())
	eq(t, "shell", got["shell"].status, "ok")
	if s := got["shell"].detail; s != "posix" && s != "wsl" && s != "gitbash" {
		t.Errorf("shell = %s", s)
	}
	wantCheck(t, got, "pkg-manager", "warn", "none")
	wantCheck(t, got, "glab", "todo", "not-installed")
	wantCheck(t, got, "curl", "todo", "not-installed")
	wantCheck(t, got, "existing", "ok", "none")
	eq(t, "package", got["package"], check{"ok", "ok", thisMachine()})
	if _, ok := got["remote"]; ok {
		t.Error("--online でないのに remote を出した")
	}
}

func TestDoctorNoNextAndDefaultCandidates(t *testing.T) {
	t.Parallel()
	d := newDoctor(t)
	// 既定の候補一覧を通すため、iso.env が入れる空文字を外す（空文字は「探さない」経路になる）。
	o := d.env()
	delete(o.Env, "CCNAVI_DOCTOR_GLAB_CANDIDATES")
	o.Unset = append(o.Unset, "CCNAVI_DOCTOR_GLAB_CANDIDATES")
	r := d.doctor(o, "--no-next")
	code(t, r, 0)
	eq(t, "次: の数", countNext(r.Stdout), 0)
	notIn(t, r.Stdout, "案内:")
	if s := checks(r.Stdout)["glab"].detail; s != "not-installed" && s != "not-on-path" {
		t.Errorf("glab = %s", s)
	}
}

func TestDoctorBadArgumentsExit2(t *testing.T) {
	t.Parallel()
	d := newDoctor(t)
	for _, args := range [][]string{{"--nope"}, {"--mode", "x"}, {"--workspace", "/no/such/dir"}} {
		code(t, d.sh(append([]string{doctorSh()}, args...), d.env(), ""), 2)
	}
	code(t, d.doctor(d.env(), "--with-extensions"), 2)
	code(t, d.doctor(d.env("CCNAVI_DOCTOR_GIT_LIMIT", "abc")), 2)
	code(t, d.doctor(d.env("CCNAVI_DOCTOR_CURL_LIMIT", "2.5")), 2)
}

func TestDoctorAZipFileAsFromIsRefused(t *testing.T) {
	t.Parallel()
	d := newDoctor(t)
	zipped := shkit.Write(t, filepath.Join(d.tmp, "a.zip"), "PK")
	r := d.sh([]string{doctorSh(), "--workspace", d.ws, "--from", zipped}, d.env(), "")
	code(t, r, 2)
	in(t, r.Stderr, "zip")
}

// ---- 道具

func TestDoctorGitMissingIsNgAndExit1(t *testing.T) {
	t.Parallel()
	d := newDoctor(t)
	d.unlink("git")
	r := d.doctor(d.env())
	code(t, r, 1)
	wantCheck(t, checks(r.Stdout), "git", "ng", "not-installed")
	in(t, r.Stdout, "次: ng の項目をユーザに直してもらって返事を待ち、doctor を打ち直す")
	eq(t, "次: の数", countNext(r.Stdout), 1)
}

func TestDoctorGitTooOldIsTodo(t *testing.T) {
	t.Parallel()
	d := newDoctor(t)
	d.fake("git", "case \"$*\" in --version) echo 'git version 2.30.2';; esac")
	wantCheck(t, checks(d.doctor(d.env()).Stdout), "git", "todo", "too-old")
}

// PATH に無い jq・glab を候補の場所（空白を含む・`*` を含む）から見つける。
func TestDoctorCandidatesNotOnPath(t *testing.T) {
	t.Parallel()
	d := newDoctor(t)
	d.unlink("jq")
	jqDir := filepath.Join(d.tmp, "cand dir")
	shkit.WriteExec(t, filepath.Join(jqDir, "jq.exe"), "#!/bin/sh\nexit 0\n")
	glab := shkit.WriteExec(t, filepath.Join(d.tmp, "gl dir", "glab"), "#!/bin/sh\nexit 0\n")
	got := checks(d.doctor(d.env(
		"CCNAVI_DOCTOR_JQ_CANDIDATES", "/nope/jq\n"+filepath.Join(jqDir, "j*.exe"),
		"CCNAVI_DOCTOR_GLAB_CANDIDATES", "\n"+filepath.Join(filepath.Dir(glab), "g*b")+"\n")).Stdout)
	wantCheck(t, got, "jq", "ng", "not-on-path")
	if !strings.HasSuffix(got["jq"].rest, "jq.exe") {
		t.Errorf("jq = %+v", got["jq"])
	}
	eq(t, "glab", got["glab"], check{"todo", "not-on-path", glab})
}

// jq が無いと、jq は ng、既存の設定は読めず、API も確かめない。
func TestDoctorWithoutJq(t *testing.T) {
	t.Parallel()
	d := newDoctor(t)
	d.unlink("jq")
	d.fakeCurl("ok")
	d.writeSettings("settings.json", `{"env":{"CCNAVI_MODE":"enable"}}`)
	got := checks(d.online("GITLAB_TOKEN", "x").Stdout)
	wantCheck(t, got, "jq", "ng", "not-installed")
	wantCheck(t, got, "existing", "warn", "unchecked")
	wantCheck(t, got, "host-api", "todo", "no-jq")
}

func TestDoctorWorkspaceNotGit(t *testing.T) {
	t.Parallel()
	d := newDoctor(t)
	plain := shkit.Mkdir(t, filepath.Join(d.tmp, "plain"))
	r := d.sh([]string{doctorSh(), "--workspace", plain, "--from", d.src, "--online"}, d.env(), "")
	got := checks(r.Stdout)
	wantCheck(t, got, "workspace", "warn", "not-git")
	wantCheck(t, got, "origin", "warn", "none")
	wantCheck(t, got, "remote", "warn", "skipped")
}

func TestDoctorPkgManagerListsAllAvailable(t *testing.T) {
	t.Parallel()
	d := newDoctor(t)
	d.fake("winget", "exit 0")
	d.fake("scoop", "exit 0")
	wantCheck(t, checks(d.doctor(d.env()).Stdout), "pkg-manager", "ok", "winget,scoop")
}

// ---- glab

func TestDoctorGlabStates(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name  string
		setup func(d *doctorEnv)
		want  check
		curl  *check
	}{
		{"古い（認証の道が無い）", func(d *doctorEnv) { d.fakeGlab(false, true) }, check{"todo", "too-old", "1.52.0"}, nil},
		{"入っていれば curl が無いのは警告", func(d *doctorEnv) { d.fakeGlab(true, true) }, check{"ok", "installed", "1.52.0"}, &check{"warn", "not-installed", ""}},
		{"動かなければ unavailable（too-old ではない）", func(d *doctorEnv) { d.fake("glab", "exit 3") }, check{"todo", "unavailable", ""}, nil},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			d := newDoctor(t)
			c.setup(d)
			got := checks(d.doctor(d.env()).Stdout)
			eq(t, "glab の状態と詳細", got["glab"].status+" "+got["glab"].detail, c.want.status+" "+c.want.detail)
			if c.want.rest != "" {
				eq(t, "glab の版", got["glab"].rest, c.want.rest)
			}
			if c.curl != nil {
				wantCheck(t, got, "curl", c.curl.status, c.curl.detail)
			}
		})
	}
}

func TestDoctorNothingCommunicatesWithoutOnline(t *testing.T) {
	t.Parallel()
	d := newDoctor(t)
	d.fakeGlab(true, true)
	d.fakeCurl("ok")
	d.doctor(d.env("GITLAB_TOKEN", "x"))
	text := d.callsText()
	notIn(t, text, "ls-remote", "glab api")
	var curls []string
	for _, line := range strings.Split(text, "\n") {
		if strings.HasPrefix(line, "curl ") {
			curls = append(curls, line)
		}
	}
	if !reflect.DeepEqual(curls, []string{"curl --version"}) {
		t.Errorf("curl の呼び出し = %v", curls)
	}
	in(t, text, "glab auth --help")
}

// ---- origin

func TestDoctorOriginKinds(t *testing.T) {
	t.Parallel()
	cases := []struct {
		url    string
		status string
		detail string
		rest   string
	}{
		{"https://user:tok@gitlab.example.com:8929/g/p.git", "ok", "https", "gitlab.example.com:8929"}, // 資格情報を隠しポートは残す
		{"http://localhost:8929/g/p.git", "ok", "http", "localhost:8929"},
		{"git@gitlab.example.com:g/p.git", "warn", "ssh", "gitlab.example.com"},
		{"/srv/repo.git", "warn", "unknown", ""},
	}
	for _, c := range cases {
		t.Run(c.url, func(t *testing.T) {
			t.Parallel()
			d := newDoctor(t)
			d.setOrigin("set-url", "origin", c.url)
			r := d.doctor(d.env())
			got := checks(r.Stdout)["origin"]
			eq(t, "origin の状態と詳細", got.status+" "+got.detail, c.status+" "+c.detail)
			if c.rest != "" {
				eq(t, "origin の宛先", got.rest, c.rest)
			}
			notIn(t, r.Stdout+r.Stderr, "tok")
		})
	}
}

func TestDoctorGithubOriginSkipsHostApi(t *testing.T) {
	t.Parallel()
	d := newDoctor(t)
	d.setOrigin("set-url", "origin", "https://github.com/o/r.git")
	got := checks(d.online().Stdout)
	wantCheck(t, got, "origin", "warn", "github")
	if _, ok := got["host-api"]; ok {
		t.Error("GitHub なのに host-api を出した")
	}
}

func TestDoctorRemoteIsSkippedWithoutOrigin(t *testing.T) {
	t.Parallel()
	d := newDoctor(t)
	d.setOrigin("remove", "origin")
	wantCheck(t, checks(d.online().Stdout), "remote", "warn", "skipped")
}

// ---- --online

func TestDoctorOnlineReadableWithoutToken(t *testing.T) {
	t.Parallel()
	d := newDoctor(t)
	d.fakeCurl("ok")
	got := checks(d.online().Stdout)
	wantCheck(t, got, "remote", "ok", "readable")
	in(t, got["remote"].rest, "push")
	in(t, d.callsText(), "ls-remote --exit-code origin HEAD")
	wantCheck(t, got, "host-api", "todo", "token-missing")
}

func TestDoctorOnlineEmptyRemoteAndNoCurl(t *testing.T) {
	t.Parallel()
	d := newDoctor(t)
	d.ctlSet("ls", "empty")
	got := checks(d.online("GITLAB_TOKEN", "x").Stdout)
	wantCheck(t, got, "remote", "ok", "readable")
	wantCheck(t, got, "host-api", "todo", "no-curl")
}

// 失敗の文に載る資格情報・トークンの形・環境のトークンを伏せる。
func TestDoctorFailureTextHidesCredentialsTokenShapesAndTheEnvToken(t *testing.T) {
	t.Parallel()
	d := newDoctor(t)
	d.ctlSet("ls", "leak")
	r := d.online("GITLAB_TOKEN", "s3cr3tvalue99")
	notIn(t, r.Stdout+r.Stderr, "abc12345xyz", "ghp_abcdefghijkl", "glrt-abcdefghij12", "s3cr3tvalue99", "u:tok")
	got := checks(r.Stdout)
	wantCheck(t, got, "remote", "todo", "unreachable")
	in(t, got["remote"].rest, "<伏せた>")
}

// curl の 401 は unauthorized で、ok にならない。失敗の種類ごとに状態を分ける。
func TestDoctorCurlFailureKinds(t *testing.T) {
	t.Parallel()
	d := newDoctor(t)
	d.fakeCurl("401")
	r := d.online("GITLAB_TOKEN", "x")
	got := checks(r.Stdout)
	wantCheck(t, got, "host-api", "todo", "unauthorized")
	wantCheck(t, got, "glab", "todo", "not-installed")
	notIn(t, r.Stdout, "optional:")
	for _, c := range []struct{ mode, want string }{{"404", "not-found"}, {"timeout", "timeout"}, {"unreach", "unreachable"}} {
		d.ctlSet("curl", c.mode)
		wantCheck(t, checks(d.online("GITLAB_TOKEN", "x").Stdout), "host-api", "todo", c.want)
	}
}

// 通る通しの代表。一時の置き場を消し、配布元を変えず、見張りの sleep を残さない。glab が無くても API に届けば glab は任意。
func TestDoctorWithoutGlabButReachableGlabIsOptional(t *testing.T) {
	t.Parallel()
	d := newDoctor(t)
	d.fakeCurl("ok")
	tmp := shkit.Mkdir(t, filepath.Join(d.tmp, "tmp"))
	before := snapshot(t, d.src)
	r := d.online("GITLAB_TOKEN", "x", "TMPDIR", tmp, "CCNAVI_DOCTOR_GIT_LIMIT", "2.719")
	got := checks(r.Stdout)
	wantCheck(t, got, "host-api", "ok", "curl")
	wantCheck(t, got, "remote", "ok", "readable")
	wantCheck(t, got, "glab", "warn", "not-installed")
	if !strings.HasPrefix(got["glab"].rest, "optional:") {
		t.Errorf("glab = %+v", got["glab"])
	}
	notIn(t, r.Stdout, "案内: glab")
	code(t, r, 0)
	if left := shkit.Names(t, tmp); len(left) != 0 {
		t.Errorf("一時の置き場に残った: %v", left)
	}
	if !sameSnapshot(snapshot(t, d.src), before) {
		t.Error("配布元が変わった")
	}
	if left := waitGone("sleep 2.719", 30); len(left) != 0 {
		t.Errorf("見張りが残った: %v", left)
	}
}

func TestDoctorAnOldGlabIsOptionalTooButKeepsTheCode(t *testing.T) {
	t.Parallel()
	d := newDoctor(t)
	d.fakeGlab(false, false)
	d.fakeCurl("ok")
	got := checks(d.online("GITLAB_TOKEN", "x").Stdout)
	wantCheck(t, got, "glab", "warn", "too-old")
	if !strings.HasPrefix(got["glab"].rest, "optional:") {
		t.Errorf("glab = %+v", got["glab"])
	}
}

func TestDoctorGlabApiIsUsedWhenItWorks(t *testing.T) {
	t.Parallel()
	d := newDoctor(t)
	d.fakeGlab(true, true)
	got := checks(d.online().Stdout)
	wantCheck(t, got, "host-api", "ok", "glab")
	wantCheck(t, got, "glab", "ok", "installed")
}

// ---- existing

// 設定の読み方。1 行に詰めた JSON・ほかのキー・文字列の中の同じ語は JSON として読み、エスケープを解き、大文字小文字を区別しない。
// settings.local.json は settings.json を上書きする。読めない JSON は invalid で、読めない場所を言う。
func TestDoctorExisting(t *testing.T) {
	t.Parallel()
	enable := `{"env":{"CCNAVI_MODE":"enable"}}`
	dry := `{"env":{"CCNAVI_MODE":"dry-run"}}`
	cases := []struct {
		name            string
		settings, local string
		want            check
	}{
		{"1 行に詰めた JSON と、文字列の中の同じ語", `{"note":"CCNAVI_MODE enable","a":1,"env":{"x":"y","CCNAVI_MODE":"dry-run"},"b":[1]}`, "", check{"ok", "dry-run", ""}},
		{"エスケープを解き、大文字小文字を区別しない", `{"env":{"CCNAVI_MODE":"\u0045nable"}}`, "", check{"warn", "enable", ""}},
		{"知らない値", `{"env":{"CCNAVI_MODE":"nope"}}`, "", check{"warn", "other", "nope"}},
		{"local が dry-run なら上書きする", enable, dry, check{"ok", "dry-run", ""}},
		{"local が enable なら上書きする", dry, enable, check{"warn", "enable", ""}},
		{"読めない JSON", `{"env": `, "", check{"warn", "invalid", ".claude/settings.json"}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			d := newDoctor(t)
			d.writeSettings("settings.json", c.settings)
			if c.local != "" {
				d.writeSettings("settings.local.json", c.local)
			}
			got := checks(d.doctor(d.env()).Stdout)["existing"]
			eq(t, "existing の状態と詳細", got.status+" "+got.detail, c.want.status+" "+c.want.detail)
			if c.want.rest != "" {
				eq(t, "existing の残り", got.rest, c.want.rest)
			}
		})
	}
}

// ---- 配布元

func TestDoctorPackageStates(t *testing.T) {
	t.Parallel()
	d := newDoctor(t)
	shkit.Remove(t, filepath.Join(d.src, "dist", "ccnavi", "ccnavi"))
	wantCheck(t, checks(d.doctor(d.env()).Stdout), "package", "ng", "no-executable")
	shkit.WriteExec(t, filepath.Join(d.src, "dist", "ccnavi", "ccnavi.exe"), "#!/bin/sh\n")
	wantCheck(t, checks(d.doctor(d.env()).Stdout), "package", "ok", "ok")
	shkit.Remove(t, filepath.Join(d.src, "dist", "ccnavi.target"))
	r := d.doctor(d.env())
	wantCheck(t, checks(r.Stdout), "package", "ng", "missing")
	code(t, r, 1)
}

func TestDoctorPackageWrongTarget(t *testing.T) {
	t.Parallel()
	d := newDoctor(t)
	other := "windows-x86_64"
	if strings.HasPrefix(thisMachine(), "windows") {
		other = "linux-x86_64"
	}
	d.makeFrom(other, true)
	got := checks(d.doctor(d.env()).Stdout)["package"]
	eq(t, "package", got.status+" "+got.detail, "ng wrong-target")
	eq(t, "目印", got.rest, other)
}

// macOS の隔離の印は読むだけで、外さない。
func TestDoctorPackageQuarantineIsReadNotCleared(t *testing.T) {
	t.Parallel()
	d := newDoctor(t)
	d.fake("xattr", `case "$*" in "-p com.apple.quarantine"*) exit 0;; esac; exit 1`)
	wantCheck(t, checks(d.doctor(d.env()).Stdout), "package", "warn", "quarantined")
	for _, line := range strings.Split(d.callsText(), "\n") {
		if strings.Contains(line, "xattr") && strings.Contains(line, "-d") {
			t.Errorf("隔離の印を外そうとした: %s", line)
		}
	}
}

func TestDoctorBuildModeItems(t *testing.T) {
	t.Parallel()
	d := newDoctor(t)
	build := filepath.Join(d.src, "scripts", "ccnavi-build.sh")
	r := d.doctor(d.env(), "--mode", "build")
	got := checks(r.Stdout)
	wantCheck(t, got, "go", "ng", "not-installed")
	wantCheck(t, got, "source", "ng", "missing")
	if _, ok := got["package"]; ok {
		t.Error("build モードで package を出した")
	}
	wantCheck(t, got, "node", "warn", "not-installed")
	shkit.Write(t, build, "")
	d.fake("go", "echo go version go1.26.0 linux/amd64")
	r = d.doctor(d.env(), "--mode", "build", "--with-extensions")
	got = checks(r.Stdout)
	eq(t, "go", got["go"], check{"ok", "ok", "1.26.0"})
	eq(t, "source", got["source"].status, "ok")
	wantCheck(t, got, "node", "ng", "not-installed")
	code(t, r, 1)
	r = d.doctor(d.env(), "--mode", "build")
	code(t, r, 0)
	in(t, lastLine(r.Stdout), "--build --source '"+d.src+"' --check")
}

// ---- 作業場の指定

func TestDoctorWorkspaceSpellings(t *testing.T) {
	t.Parallel()
	d := newDoctor(t)
	t.Run(".git の中は作業ツリーではない", func(t *testing.T) {
		r := d.sh([]string{doctorSh(), "--workspace", filepath.Join(d.ws, ".git"), "--from", d.src}, d.env(), "")
		wantCheck(t, checks(r.Stdout), "workspace", "warn", "not-git")
	})
	t.Run("- で始まる名前も見つかる", func(t *testing.T) {
		shkit.Mkdir(t, filepath.Join(d.tmp, "-odd"))
		r := d.sh([]string{doctorSh(), "--workspace", "-odd", "--from", d.src}, d.env(), d.tmp)
		code(t, r, 0)
	})
}

// ---- 時間の上限と後始末

func (d *doctorEnv) slowOpts(lsSleep, limit string) run.Opts {
	d.ctlSet("ls", "sleep")
	d.ctlSet("ls_sleep", lsSleep)
	return d.env("CCNAVI_DOCTOR_GIT_LIMIT", limit)
}

func TestDoctorTimeoutEndsAndLeavesNoWatcher(t *testing.T) {
	t.Parallel()
	d := newDoctor(t)
	o := d.slowOpts("61.311", "2.718")
	start := time.Now()
	r := d.doctor(o, "--online")
	took := time.Since(start)
	wantCheck(t, checks(r.Stdout), "remote", "todo", "timeout")
	if took > 45*time.Second { // 偽の git は 61 秒眠る。上限で終わったことだけを見る（負荷で遅れても落とさない）
		t.Errorf("上限のあとも %v かかった", took)
	}
	if left := waitGone("sleep 2.718", 30); len(left) != 0 {
		t.Errorf("見張りが残った: %v", left)
	}
}

// signalRun は --online の実行中に信号を送り、終了コードを返す。偽の git の sleep が起きるのを待ってから送る。
func (d *doctorEnv) signalRun(sig os.Signal, needle, limit string) int {
	d.t.Helper()
	o := d.slowOpts(needle, limit)
	// 子の信号の扱いを既定にそろえる。Go が SIGINT を捕まえていれば、背景で起こされて無視されている状態を子へ渡さない。
	notify := make(chan os.Signal, 1)
	signal.Notify(notify, os.Interrupt)
	defer signal.Stop(notify)
	cmd := exec.Command(filepath.Join(d.bin, "sh"), doctorSh(), "--workspace", d.ws, "--from", d.src, "--online")
	cmd.Env = run.Environment(o)
	if err := cmd.Start(); err != nil {
		d.t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	end := time.Now().Add(60 * time.Second)
	for time.Now().Before(end) && len(running("sleep "+needle)) == 0 {
		time.Sleep(100 * time.Millisecond)
	}
	if len(running("sleep "+needle)) == 0 {
		_ = cmd.Process.Kill()
		d.t.Fatal("偽の git が起動しなかった")
	}
	_ = cmd.Process.Signal(sig)
	select {
	case <-done:
	case <-time.After(60 * time.Second):
		_ = cmd.Process.Kill()
		d.t.Fatal("信号のあとも終わらなかった")
	}
	return cmd.ProcessState.ExitCode()
}

// INT・TERM で終わるときも、見張りの sleep を残さず、決まった終了コード（130・143）で終わる。
func TestDoctorSignalsCleanUp(t *testing.T) {
	t.Parallel()
	if !base.CanSymlink() {
		t.Skip("シンボリックリンクを作れない")
	}
	cases := []struct {
		name   string
		sig    os.Signal
		needle string
		limit  string
		want   int
	}{
		{"INT", os.Interrupt, "61.313", "30.123", 130},
		{"TERM", syscall.SIGTERM, "61.314", "30.124", 143},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			d := newDoctor(t)
			eq(t, "終了コード", d.signalRun(c.sig, c.needle, c.limit), c.want)
			if left := waitGone("sleep "+c.limit, 30); len(left) != 0 {
				t.Errorf("見張りが残った: %v", left)
			}
		})
	}
}

// host 部品を単体で `.` 読み込みする使い方（doctor の使い方）が成り立つ。host 部品がほかの部品の関数に頼るようになったら、ここが落ちる。
func TestHostPartLoadsAloneAndHasTheFunctions(t *testing.T) {
	t.Parallel()
	e := newIso(t)
	e.link("jq")
	hostSh := filepath.Join(base.ScriptsDir(), "ccnavi-common-host.sh")
	names := "ccnavi_host_parse ccnavi_host_connect ccnavi_host_api ccnavi_host_encoded_path"
	probe := ". \"" + hostSh + "\"\n" +
		"for f in " + names + " ccnavi_git_auth; do\n" +
		"  command -v \"$f\" >/dev/null || { echo \"missing $f\"; exit 1; }\n" +
		"done\n" +
		"ccnavi_host_parse \"https://u:tok@GitLab.example.com:8929/g/p.git\" || exit 2\n" +
		"printf \"%s|%s|%s|%s\\n\" \"$ccnavi_h_scheme\" \"$ccnavi_h_host\" \"$ccnavi_h_kind\" \"$ccnavi_h_path\"\n"
	r := e.sh([]string{"-c", probe}, e.env(), "")
	code(t, r, 0)
	eq(t, "解いた結果", strings.TrimSpace(r.Stdout), "https|GitLab.example.com:8929|gitlab|g/p")
	notIn(t, r.Stdout, "tok")
}
