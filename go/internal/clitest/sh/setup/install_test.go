package setup

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

// scripts/ccnavi-install.sh の受入テスト。外から動かし、出力・終了コード・書かれたものを読む。配布元は偽物の組み立て
// （ccnavi-setup.sh は引数を記録する偽物。doctor と host 部品は本物）。道具は隔離した bin だけを PATH にする。

type installEnv struct {
	*iso
	ws, src string
}

func installSh() string { return scriptPath("ccnavi-install.sh") }

func newInstall(t *testing.T) *installEnv {
	t.Helper()
	i := &installEnv{iso: newIso(t)}
	i.link("jq")
	i.fakeGit()
	i.ws = i.gitRepo(filepath.Join(i.tmp, "ws"), "https://u:tok@gl.example.com/g/p.git")
	i.src = i.makeSrc(filepath.Join(i.tmp, "src"))
	return i
}

// makeSrc は偽の配布元。
func (i *installEnv) makeSrc(root string) string {
	i.t.Helper()
	for _, name := range []string{"ccnavi-doctor.sh", "ccnavi-install.sh"} {
		copyFile(i.t, scriptPath(name), filepath.Join(root, "scripts", name), 0o644)
	}
	copyFile(i.t, filepath.Join(base.ScriptsDir(), "ccnavi-common-host.sh"), filepath.Join(root, ".ccnavi", "scripts", "ccnavi-common-host.sh"), 0o644)
	write(i.t, filepath.Join(root, "dist", "ccnavi.target"), thisMachine()+"\n")
	writeExec(i.t, filepath.Join(root, "dist", "ccnavi", "ccnavi"), "#!/bin/sh\nexit 0\n")
	write(i.t, filepath.Join(root, ".claude", "skills", "foo", "SKILL.md"), "new foo\n")
	writeExec(i.t, filepath.Join(root, "scripts", "ccnavi-setup.sh"), i.setupText())
	return root
}

// setupText は偽の setup。呼ばれたら記録し、ctl の check_rc・setup_rc・launcher_rc で終了コードを決める。
func (i *installEnv) setupText() string {
	launcher := "#!/bin/sh\n" +
		"printf 'launcher %s\\n' \"$*\" >> \"" + i.calls + "\"\n" +
		"printf \"error 0 件、warn 2 件\\n\"\n" +
		"exit \"$(cat \"" + i.ctlRef("launcher_rc") + "\" 2>/dev/null || echo 0)\"\n"
	m := thisMachine()
	return "printf 'setup %s\\n' \"$*\" >> \"" + i.calls + "\"\n" +
		"ws=$1\n" +
		"case \"$*\" in\n" +
		"*--check*)\n" +
		"\trc=$(cat \"" + i.ctlRef("check_rc") + "\" 2>/dev/null || echo 0)\n" +
		"\tprintf \"(setup check)\\n\"\n" +
		"\texit \"$rc\"\n" +
		"\t;;\n" +
		"esac\n" +
		"rc=$(cat \"" + i.ctlRef("setup_rc") + "\" 2>/dev/null || echo 0)\n" +
		"[ \"$rc\" = 0 ] || { echo \"setup failed\" >&2; exit \"$rc\"; }\n" +
		"mkdir -p \"$ws/.ccnavi/scripts\" \"$ws/.ccnavi/bin/" + m + "\" \"$ws/.claude\"\n" +
		"printf '%s' " + shellQuote(launcher) + " > \"$ws/.ccnavi/scripts/ccnavi-launcher.sh\"\n" +
		"printf \"#!/bin/sh\\n\" > \"$ws/.ccnavi/bin/" + m + "/ccnavi\"\n" +
		"chmod 644 \"$ws/.ccnavi/bin/" + m + "/ccnavi\"\n" +
		"[ -f \"$ws/.claude/settings.json\" ] || \\\n" +
		"\techo '{\"env\":{\"CCNAVI_MODE\":\"dry-run\"}}' > \"$ws/.claude/settings.json\"\n" +
		"exit 0\n"
}

func (i *installEnv) install(args ...string) run.Result {
	i.t.Helper()
	return i.installAs(i.env(), i.ws, args...)
}

func (i *installEnv) installAs(o run.Opts, ws string, args ...string) run.Result {
	i.t.Helper()
	return i.sh(append([]string{installSh(), ws}, args...), o, "")
}

// setupCalls は偽の setup が受け取った引数（呼び出しごと）。
func (i *installEnv) setupCalls() [][]string {
	var out [][]string
	for _, line := range strings.Split(i.callsText(), "\n") {
		if rest, ok := strings.CutPrefix(line, "setup "); ok {
			out = append(out, strings.Split(rest, " "))
		}
	}
	return out
}

func (i *installEnv) writeSettings(name, text string) {
	write(i.t, filepath.Join(i.ws, ".claude", name), text)
}

func (i *installEnv) oneNext(r run.Result) {
	i.t.Helper()
	if n := countNext(r.Stdout); n != 1 {
		i.t.Errorf("次: の数 = %d\n%s", n, r.Stdout)
	}
}

func modeSettings(value string) string {
	b, _ := json.Marshal(map[string]any{"env": map[string]string{"CCNAVI_MODE": value}})
	return string(b)
}

func (i *installEnv) backups() []string {
	root := filepath.Join(i.home, ".cache", "ccnavi", "backup")
	if !exists(root) {
		return nil
	}
	var out []string
	for _, n := range names(i.t, root) {
		out = append(out, filepath.Join(root, n))
	}
	return out
}

// ---- 配布元の決め方

// --from が 1 つ下の配布元を指すときはそれを使う。候補が 2 つ・無いときは 3 で「次:」を 1 つ出して止まる。zip は 2。
func TestInstallResolvesTheSource(t *testing.T) {
	t.Parallel()
	t.Run("1 つ下に配布元がある", func(t *testing.T) {
		t.Parallel()
		i := newInstall(t)
		parent := mkdir(t, filepath.Join(i.tmp, "parent"))
		inner := i.makeSrc(filepath.Join(parent, "pkg"))
		r := i.install("--from", parent, "--check")
		code(t, r, 0)
		if got := i.setupCalls()[0][:3]; !reflect.DeepEqual(got, []string{i.ws, "--deploy", inner}) {
			t.Errorf("setup の引数 = %v", got)
		}
	})
	t.Run("候補が 2 つ・無いのは 3", func(t *testing.T) {
		t.Parallel()
		i := newInstall(t)
		two := mkdir(t, filepath.Join(i.tmp, "two"))
		i.makeSrc(filepath.Join(two, "a"))
		i.makeSrc(filepath.Join(two, "b"))
		none := mkdir(t, filepath.Join(i.tmp, "empty"))
		for _, from := range []string{two, none} {
			r := i.install("--from", from, "--check")
			code(t, r, 3)
			i.oneNext(r)
		}
		if len(i.setupCalls()) != 0 {
			t.Error("setup を呼んだ")
		}
	})
	t.Run("zip は 2", func(t *testing.T) {
		t.Parallel()
		i := newInstall(t)
		r := i.install("--from", write(t, filepath.Join(i.tmp, "a.zip"), "PK"), "--check")
		code(t, r, 2)
		in(t, r.Stderr, "zip")
	})
	t.Run("既定は打ったスクリプトの 1 つ上", func(t *testing.T) {
		t.Parallel()
		i := newInstall(t)
		r := i.sh([]string{filepath.Join(i.src, "scripts", "ccnavi-install.sh"), i.ws, "--check"}, i.env(), "")
		code(t, r, 0)
		eq(t, "配布元", i.setupCalls()[0][2], i.src)
	})
	t.Run("配布先が配布元の中に展開した配布元は良い", func(t *testing.T) {
		t.Parallel()
		i := newInstall(t)
		inner := i.makeSrc(filepath.Join(i.ws, "unpacked"))
		code(t, i.install("--from", inner, "--check"), 0)
	})
}

// 配布先が配布元そのもの・リンク・配布元の中なら 3 で止まる（配布元を変えない）。
func TestInstallRefusesAWorkspaceThatIsTheSource(t *testing.T) {
	t.Parallel()
	i := newInstall(t)
	link := filepath.Join(i.tmp, "link-to-src")
	symlink(t, i.src, link)
	before := snapshot(t, i.src)
	for _, ws := range []string{i.src, link, filepath.Join(i.src, "scripts")} {
		r := i.installAs(i.env(), ws, "--from", i.src)
		code(t, r, 3)
		i.oneNext(r)
	}
	if !sameSnapshot(snapshot(t, i.src), before) {
		t.Error("配布元が変わった")
	}
}

func TestInstallArgumentErrorsAre2(t *testing.T) {
	t.Parallel()
	i := newInstall(t)
	cases := [][]string{
		{"--nope"},
		{"--mode", "disable", "--from", i.src},
		{"--with-extensions", "--from", i.src},
		{"--source", i.src, "--from", i.src},
	}
	for _, args := range cases {
		code(t, i.install(args...), 2)
	}
	code(t, i.sh([]string{installSh()}, i.env(), ""), 2)
	for _, bad := range []string{"--from", "--mode", "--ticket-control", "--clear-quarantine", "--force"} {
		args := []string{"--build", bad, "x"}
		if bad == "--clear-quarantine" || bad == "--force" {
			args = []string{"--build", bad}
		}
		code(t, i.install(args...), 2)
	}
}

// ---- --check と配布元

// --check の通しの代表。配布元を直に指し、enable でも Claude Code の外なら進み、配布元を変えず、資格情報を出さない。
func TestInstallCheckRun(t *testing.T) {
	t.Parallel()
	i := newInstall(t)
	i.writeSettings("settings.json", `{"env":{"CCNAVI_MODE":"enable"}}`)
	before := snapshot(t, i.src)
	r := i.install("--from", i.src, "--check")
	code(t, r, 0)
	i.oneNext(r)
	in(t, r.Stdout, "すでに揃っている")
	if got := i.setupCalls()[0][:3]; !reflect.DeepEqual(got, []string{i.ws, "--deploy", i.src}) {
		t.Errorf("setup の引数 = %v", got)
	}
	if !sameSnapshot(snapshot(t, i.src), before) {
		t.Error("配布元が変わった")
	}
	notIn(t, r.Stdout+r.Stderr, "tok")
}

// ---- doctor との関係

func TestInstallNgStopsBeforeSetup(t *testing.T) {
	t.Parallel()
	i := newInstall(t)
	i.unlink("git")
	r := i.install("--from", i.src)
	code(t, r, 3)
	if len(i.setupCalls()) != 0 {
		t.Error("setup を呼んだ")
	}
	i.oneNext(r)
	in(t, r.Stdout, "check: git ng not-installed")
}

// setup に渡るのは配布元と作業場のほかは 5 つのオプションだけ。
func TestInstallOnlyFiveOptionsReachSetup(t *testing.T) {
	t.Parallel()
	i := newInstall(t)
	r := i.install("--from", i.src, "--mode", "enable", "--ticket-control", "disable",
		"--force", "--no-vscode", "--no-fetch", "--no-idle", "--clear-quarantine", "--check")
	code(t, r, 0)
	want := []string{i.ws, "--deploy", i.src, "--mode", "enable", "--ticket-control", "disable", "--force", "--no-vscode", "--no-fetch", "--no-idle", "--check"}
	if got := i.setupCalls()[0]; !reflect.DeepEqual(got, want) {
		t.Errorf("setup の引数 = %v\nwant %v", got, want)
	}
}

func (i *installEnv) prepareInstalled() {
	i.t.Helper()
	for rel, text := range map[string]string{
		".ccnavi/CCNAVI.md":                 "old md\n",
		".ccnavi/scripts/ccnavi-x.sh":       "old sh\n",
		".ccnavi/docs/a.md":                 "old doc\n",
		".ccnavi/config/common/rules.yml":   "old rules\n",
		".claude/skills/foo/SKILL.md":       "old foo\n",
		".claude/skills/mine/SKILL.md":      "mine\n",
		".vscode/settings.json":             "{}\n",
		".ccnavi/config/project/phases.yml": "mine\n",
	} {
		write(i.t, filepath.Join(i.ws, filepath.FromSlash(rel)), text)
	}
	i.writeSettings("settings.json", `{"env":{"CCNAVI_MODE":"dry-run"}}`)
}

// 本番の通しの代表。todo だけなら進み、setup には作業場と --deploy だけを渡し、入った実行ファイルだけに実行の権限を付け、lint を打ち、
// 拡張の案内を出し、--force なしではバックアップを取らず、配布元を変えない。
func TestInstallPlainRealRun(t *testing.T) {
	t.Parallel()
	i := newInstall(t)
	i.prepareInstalled()
	write(t, filepath.Join(i.src, "dist", "ccnavi-board-1.0.0.vsix"), "x")
	write(t, filepath.Join(i.src, "extensions", "chrome", "ccnavi-approval", "dist", "manifest.json"), "{}")
	before := snapshot(t, i.src)
	r := i.install("--from", i.src)
	code(t, r, 0)
	in(t, r.Stdout, "check: glab todo not-installed", "案内: glab が todo（not-installed）")
	if got := i.setupCalls(); !reflect.DeepEqual(got, [][]string{{i.ws, "--deploy", i.src}}) {
		t.Errorf("setup の呼び出し = %v", got)
	}
	i.oneNext(r)
	exe := filepath.Join(i.ws, ".ccnavi", "bin", thisMachine(), "ccnavi")
	if !canExec(exe) {
		t.Error("実行ファイルに実行の権限が付いていない")
	}
	if exists(exe + ".exe") {
		t.Error(".exe を作った")
	}
	in(t, i.callsText(), "launcher --lint --root "+i.ws+" --log  --state ")
	in(t, r.Stdout, "dry-run では lint の warn", "code --install-extension", "パッケージ化されていない拡張機能を読み込む", "そろえて更新する決まり")
	in(t, lastLine(r.Stdout), "Claude Code を終了して", i.ws)
	if len(i.backups()) != 0 {
		t.Error("--force なしでバックアップを取った")
	}
	if !sameSnapshot(snapshot(t, i.src), before) {
		t.Error("配布元が変わった")
	}
}

// ---- setup の終了コード

// setup --check の 0 は TestInstallCheckRun が見る。1 は「了承を得てから --check を外して打つ」で終わり（--force でもバックアップを取らない）、
// ほかは 4。
func TestInstallCheckExitCodesAndNextText(t *testing.T) {
	t.Parallel()
	i := newInstall(t)
	i.prepareInstalled()
	i.ctlSet("check_rc", "1")
	r := i.install("--from", i.src, "--check", "--force")
	code(t, r, 1)
	i.oneNext(r)
	in(t, r.Stdout, "了承を得てから、--check を外して打つ", "references/update.md")
	if len(i.backups()) != 0 {
		t.Error("--check でバックアップを取った")
	}
	i.ctlSet("check_rc", "2")
	r = i.install("--from", i.src, "--check")
	code(t, r, 4)
	i.oneNext(r)
	in(t, r.Stdout, "setup の出力の理由を直してから")
}

func TestInstallRealRunSetupFailureIs4(t *testing.T) {
	t.Parallel()
	i := newInstall(t)
	i.ctlSet("setup_rc", "2")
	r := i.install("--from", i.src)
	code(t, r, 4)
	in(t, r.Stderr, "setup failed")
	i.oneNext(r)
	for _, line := range strings.Split(i.callsText(), "\n") {
		if strings.HasPrefix(line, "launcher") {
			t.Errorf("lint を起こした: %s", line)
		}
	}
}

// ---- 入れてよい状態

// Claude Code の中で enable のまま入れるのは 3 で止める。dry-run 以外の値は enable として扱う（保存された値も起動した環境の値も、
// 大文字小文字と前後の空白を無視して比べる）。保存された値が無ければ進み、環境の値は数える。local の dry-run は enable を上書きする。
// 読めない JSON は 3。
func TestInstallModeInsideClaudeCode(t *testing.T) {
	t.Parallel()
	enable := `{"env":{"CCNAVI_MODE":"enable"}}`
	cases := []struct {
		name            string
		settings, local string
		env             []string
		want            int
		next            string
	}{
		{"enable は Claude Code の中では 3", enable, "", []string{"CLAUDECODE", "1"}, 3, "Claude Code の外のターミナルで同じコマンドを打ってもらう"},
		{"disable も enable として数える", modeSettings("disable"), "", []string{"CLAUDECODE", "1"}, 3, ""},
		{"大文字小文字と空白を無視した dry-run は進む", modeSettings(" DRY-RUN "), "", []string{"CLAUDECODE", "1", "CCNAVI_MODE", " Dry-Run "}, 0, ""},
		{"保存された値が無ければ進む", "", "", []string{"CLAUDECODE", "1"}, 0, ""},
		{"保存された値が無くても環境の値は数える", "", "", []string{"CLAUDECODE", "1", "CCNAVI_MODE", "enable"}, 3, ""},
		{"local の dry-run が enable を上書きする", enable, `{"env":{"CCNAVI_MODE":"dry-run"}}`, []string{"CLAUDECODE", "1"}, 0, ""},
		{"読めない JSON は 3", `{"env": `, "", nil, 3, ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			i := newInstall(t)
			if c.settings != "" {
				i.writeSettings("settings.json", c.settings)
			}
			if c.local != "" {
				i.writeSettings("settings.local.json", c.local)
			}
			r := i.installAs(i.env(c.env...), i.ws, "--from", i.src, "--check")
			code(t, r, c.want)
			if c.want == 3 {
				i.oneNext(r)
				if len(i.setupCalls()) != 0 {
					t.Error("setup を呼んだ")
				}
			}
			if c.next != "" {
				in(t, r.Stdout, c.next)
			}
		})
	}
}

// ---- --build

func (i *installEnv) makeBuildSrc() string {
	i.t.Helper()
	i.fake("go", "echo go version go1.26.0 linux/amd64")
	for _, name := range []string{"build", "build-install"} {
		writeExec(i.t, filepath.Join(i.src, "scripts", "ccnavi-"+name+".sh"),
			"#!/bin/sh\nprintf '"+name+" %s\\n' \"$*\" >> \""+i.calls+"\"\n")
	}
	return i.src
}

func (i *installEnv) installFrom(src string, args ...string) run.Result {
	i.t.Helper()
	return i.sh(append([]string{filepath.Join(src, "scripts", "ccnavi-install.sh"), i.ws}, args...), i.env(), "")
}

func TestInstallBuildCheckRunsNothing(t *testing.T) {
	t.Parallel()
	i := newInstall(t)
	src := i.makeBuildSrc()
	before := snapshot(t, src)
	r := i.installFrom(src, "--build", "--check")
	code(t, r, 0)
	notIn(t, i.callsText(), "pull", "build ", "build-install", "setup ")
	i.oneNext(r)
	in(t, r.Stdout, "git pull --ff-only", "了承を得てから、--check を外して打つ")
	if !sameSnapshot(snapshot(t, src), before) {
		t.Error("配布元が変わった")
	}
	i.fake("node", "echo v22.0.0")
	i.fake("pnpm", "echo 10.0.0")
	r = i.installFrom(src, "--build", "--check", "--with-extensions")
	in(t, r.Stdout, "VS Code への拡張機能のインストール")
}

func TestInstallBuildWithExtensionsCallsOnlyBuildInstall(t *testing.T) {
	t.Parallel()
	i := newInstall(t)
	src := i.makeBuildSrc()
	i.fake("node", "echo v22.0.0")
	i.fake("pnpm", "echo 10.0.0")
	r := i.installFrom(src, "--build", "--with-extensions")
	code(t, r, 0)
	calls := i.callsText()
	in(t, calls, "build-install")
	notIn(t, calls, "build \n", "pull")
}

// --build は build.sh を打ち、追跡するブランチがあるときだけ pull する。
func TestInstallBuildRunsTheBuildShAndPullsOnlyWithATrackingBranch(t *testing.T) {
	t.Parallel()
	i := newInstall(t)
	src := i.makeBuildSrc()
	i.gitRepo(src, "https://gl.example.com/g/ccnavi.git")
	run.Git(t, src, "commit", "--allow-empty", "-q", "-m", "x")
	r := i.installFrom(src, "--build")
	code(t, r, 0)
	calls := i.callsText()
	in(t, calls, "build \n")
	notIn(t, calls, "setup ", "pull")
	i.oneNext(r)
	in(t, lastLine(r.Stdout), "--from '"+src+"' --check")
	branch := run.Git(t, src, "rev-parse", "--abbrev-ref", "HEAD")
	run.Git(t, src, "update-ref", "refs/remotes/origin/"+branch, "HEAD")
	run.Git(t, src, "config", "branch."+branch+".remote", "origin")
	run.Git(t, src, "config", "branch."+branch+".merge", "refs/heads/"+branch)
	r = i.installFrom(src, "--build")
	code(t, r, 0)
	in(t, i.callsText(), "pull --ff-only")
}

func TestInstallBuildFailureIs4(t *testing.T) {
	t.Parallel()
	i := newInstall(t)
	src := i.makeBuildSrc()
	writeExec(t, filepath.Join(src, "scripts", "ccnavi-build.sh"), "#!/bin/sh\nexit 1\n")
	r := i.installFrom(src, "--build")
	code(t, r, 4)
	i.oneNext(r)
}

// ---- --force のバックアップ

// バックアップは作業場の外に 700 で取り、リンクの先の中身を写す。ユーザ自身のスキル・自身のレイヤーは写さない。配布元は変えない。
func TestInstallForceBacksUpOutsideTheWorkspaceWithMode700(t *testing.T) {
	t.Parallel()
	i := newInstall(t)
	i.prepareInstalled()
	// .ccnavi/scripts をリンクにする（バックアップにはリンクでなく中身を残す）。
	shared := filepath.Join(i.tmp, "shared-scripts")
	scripts := filepath.Join(i.ws, ".ccnavi", "scripts")
	if err := os.Rename(scripts, shared); err != nil {
		t.Fatal(err)
	}
	symlink(t, shared, scripts)
	before := snapshot(t, i.src)
	r := i.install("--from", i.src, "--force", "--clear-quarantine")
	code(t, r, 0)
	found := i.backups()
	if len(found) != 1 {
		t.Fatalf("バックアップ = %v", found)
	}
	if strings.HasPrefix(found[0], i.ws) {
		t.Error("バックアップが作業場の中にある")
	}
	if info, _ := os.Stat(found[0]); info.Mode().Perm() != 0o700 {
		t.Errorf("mode = %v", info.Mode().Perm())
	}
	for rel, text := range map[string]string{
		".ccnavi/CCNAVI.md":               "old md\n",
		".ccnavi/scripts/ccnavi-x.sh":     "old sh\n",
		".ccnavi/docs/a.md":               "old doc\n",
		".ccnavi/config/common/rules.yml": "old rules\n",
		".claude/skills/foo/SKILL.md":     "old foo\n",
		".vscode/settings.json":           "{}\n",
	} {
		eq(t, rel, read(t, filepath.Join(found[0], filepath.FromSlash(rel))), text)
	}
	if exists(filepath.Join(found[0], ".claude", "skills", "mine")) || exists(filepath.Join(found[0], ".ccnavi", "config", "project")) {
		t.Error("ユーザ自身のものまで写した")
	}
	in(t, r.Stdout, found[0], "cp -R")
	calls := i.setupCalls()
	eq(t, "setup の最後の引数", calls[len(calls)-1][len(calls[len(calls)-1])-1], "--force")
	if isLink(filepath.Join(found[0], ".ccnavi", "scripts")) {
		t.Error("バックアップにリンクを残した")
	}
	if !sameSnapshot(snapshot(t, i.src), before) {
		t.Error("配布元が変わった")
	}
}

// 同じ秒に 2 回打っても衝突しない。
func TestInstallTwoRunsInTheSameSecondDoNotCollide(t *testing.T) {
	t.Parallel()
	i := newInstall(t)
	i.prepareInstalled()
	i.fake("date", "echo 20260101-000000")
	first := i.install("--from", i.src, "--force")
	second := i.install("--from", i.src, "--force")
	if first.Code != 0 || second.Code != 0 {
		t.Fatalf("code = %d, %d\n%s", first.Code, second.Code, second.Stdout)
	}
	eq(t, "バックアップの数", len(i.backups()), 2)
}

// バックアップの置き場が使えない・相対のときは、setup の前に 4 で止まる。
func TestInstallAnUnusableBackupPlaceStopsWith4BeforeSetup(t *testing.T) {
	t.Parallel()
	t.Run("置き場の名前にファイルがある", func(t *testing.T) {
		t.Parallel()
		i := newInstall(t)
		i.prepareInstalled()
		write(t, filepath.Join(i.home, ".cache", "ccnavi"), "a file where the directory should be\n")
		r := i.install("--from", i.src, "--force")
		code(t, r, 4)
		if len(i.setupCalls()) != 0 {
			t.Error("setup を呼んだ")
		}
		i.oneNext(r)
	})
	t.Run("相対の XDG_CACHE_HOME は断る", func(t *testing.T) {
		t.Parallel()
		i := newInstall(t)
		i.prepareInstalled()
		o := i.env("XDG_CACHE_HOME", "cache")
		o.Dir = i.ws
		r := i.sh([]string{installSh(), i.ws, "--from", i.src, "--force"}, o, i.ws)
		code(t, r, 4)
		if exists(filepath.Join(i.ws, "cache")) {
			t.Error("相対の置き場を作った")
		}
		if len(i.setupCalls()) != 0 {
			t.Error("setup を呼んだ")
		}
	})
}

// ---- 入れたあと（通る形は TestInstallPlainRealRun が見る）

// lint は 0 と 1 のほかは、どれも起動できなかったと読む（4）。1 は 5 で、config のスキルを案内する。
func TestInstallLintOutcomes(t *testing.T) {
	t.Parallel()
	i := newInstall(t)
	i.ctlSet("launcher_rc", "127")
	r := i.install("--from", i.src)
	code(t, r, 4)
	i.oneNext(r)
	i.ctlSet("launcher_rc", "1")
	r = i.install("--from", i.src)
	code(t, r, 5)
	i.oneNext(r)
	in(t, r.Stdout, "ccnavi-config")
	in(t, lastLine(r.Stdout), "lint の error を直す")
}

// ---- macOS の隔離の印

func (i *installEnv) fakeXattr(where string) {
	i.t.Helper()
	flag := write(i.t, filepath.Join(i.ctl, "q"), "1")
	i.fake("xattr", "case \"$*\" in\n"+
		"\"-p com.apple.quarantine \"*"+where+"*) [ -f \""+flag+"\" ] && exit 0; exit 1;;\n"+
		"\"-dr com.apple.quarantine \"*) rm -f \""+flag+"\"; exit 0;;\n"+
		"esac\nexit 1")
}

func TestInstallQuarantine(t *testing.T) {
	t.Parallel()
	t.Run("頼まれなければ外さない", func(t *testing.T) {
		t.Parallel()
		i := newInstall(t)
		i.fakeXattr("/.ccnavi/bin/")
		r := i.install("--from", i.src)
		code(t, r, 0)
		notIn(t, i.callsText(), "xattr -dr")
		in(t, r.Stdout, "--clear-quarantine")
	})
	t.Run("頼まれたら作業場の中だけ外す", func(t *testing.T) {
		t.Parallel()
		i := newInstall(t)
		i.fakeXattr("/.ccnavi/bin/")
		r := i.install("--from", i.src, "--clear-quarantine")
		code(t, r, 0)
		binDir := filepath.Join(i.ws, ".ccnavi", "bin", thisMachine())
		in(t, i.callsText(), "xattr -dr com.apple.quarantine "+binDir)
		for _, line := range strings.Split(i.callsText(), "\n") {
			if strings.Contains(line, "-dr") && strings.Contains(line, i.src) {
				t.Errorf("配布元の印を外そうとした: %s", line)
			}
		}
	})
	t.Run("配布元の印は --check で知らせるだけ", func(t *testing.T) {
		t.Parallel()
		i := newInstall(t)
		i.fakeXattr("/dist/")
		before := snapshot(t, i.src)
		r := i.install("--from", i.src, "--check")
		in(t, r.Stdout, "本番では --clear-quarantine が要る")
		notIn(t, i.callsText(), "-dr")
		if !sameSnapshot(snapshot(t, i.src), before) {
			t.Error("配布元が変わった")
		}
	})
}
