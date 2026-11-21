package setup

import (
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"runtime"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/shkit"
	"ccnavi/internal/infra/env"
)

// 振り分けの sh・実行ファイルの置き場と、配る・配らない・入れ替える・断る。

// 振り分けの sh は .ccnavi/scripts/ へ、中身を変えずに配り、実行ビットを付ける。hook は sh を `sh` 経由でなく直に起動する。
// 実行ビットが無いと 126 で起動せず、判定が 1 行も走らない。sh の隣に機械ごとの置き場を作らない（sh はそこを探さない）。
func TestDeployDeploysTheLauncherNextToTheGateScripts(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	code(t, w.setup("--deploy", makeSource(t, srcOpts{})), 0)
	eq(t, "振り分けの sh", shkit.Read(t, w.launcher()), shkit.Read(t, launcherPath()))
	if runtime.GOOS != "windows" && !canExec(w.launcher()) {
		t.Error("実行ビットが付いていない")
	}
	if shkit.Exists(w.path(".ccnavi", "scripts", thisMachine())) {
		t.Error("sh の隣に機械ごとの置き場を作った")
	}
}

// 実行ファイルは .ccnavi/bin/<built>/ に置く。置き場は固定。設定に書くパスと実体の置き場が食い違うと、設定は書けているのに
// hook がどこにも無いものを起動する形になる。
func TestDeployPutsTheExecutableInTheFixedPlace(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	r := w.setup("--deploy", makeSource(t, srcOpts{}))
	code(t, r, 0)
	eq(t, "CCNAVI_ENTRY_PATH", w.envOf()["CCNAVI_ENTRY_PATH"], any(binPath))
	if !shkit.Exists(w.built(thisMachine(), "ccnavi")) {
		t.Error("実行ファイルを配っていない")
	}
	if got := shkit.Names(t, w.path(".ccnavi", "bin")); !reflect.DeepEqual(got, []string{thisMachine()}) {
		t.Errorf("bin/ の中 = %v", got)
	}
	notIn(t, r.Stdout, "まだ無いもの")
}

// `--bin` が受け取るのは project か user だけ。パスは受け取らない（配置先は固定）。2 で断り、何も書かない。
func TestDeployRefusesABinValueItDoesNotKnowAndWritesNothing(t *testing.T) {
	t.Parallel()
	src := makeSource(t, srcOpts{})
	cases := []struct {
		args    []string
		message string
	}{
		{[]string{"--bin", binPath}, "--bin に指定できるのは project か user です"},
		{[]string{"--bin"}, "--bin に値がありません"},
	}
	for _, c := range cases {
		w := newTarget(t)
		w.makeGit()
		r := w.setup(append([]string{"--mode", "enable", "--deploy", src}, c.args...)...)
		code(t, r, 2)
		in(t, r.Stderr, c.message)
		if got := shkit.Names(t, w.dir); !reflect.DeepEqual(got, []string{".git"}) {
			t.Errorf("%v: 何かを書いた: %v", c.args, got)
		}
	}
}

func TestDeployRefusesBinUserTogetherWithNoDeploy(t *testing.T) {
	t.Parallel()
	r := newTarget(t).setup("--no-deploy", "--bin", "user")
	code(t, r, 2)
	in(t, r.Stderr, "--bin user と --no-deploy は同時に指定できません")
}

// ---- --bin user（ホームディレクトリの下に置く。ターミナルで確認する）

// userBin は `--bin user` で配る。answer があれば、ターミナル（疑似ターミナル）に答えを入力する。無ければターミナルなし。
// 疑似ターミナルは `script` で与える（Linux は `script -qec`、macOS は `script -q /dev/null`）。無い環境と Windows は飛ばす。
// 疑似ターミナルの出力は標準出力と標準エラーが混ざる（両方に同じ文字列を入れて返す）。
func (w *target) userBin(home string, answer *string, extra map[string]string, args ...string) run.Result {
	w.t.Helper()
	extra = withHome(home, extra)
	argv := append([]string{scriptPath("ccnavi-setup.sh"), w.dir}, args...)
	if answer == nil {
		return run.Command(w.t, "sh", argv, runOpts(extra))
	}
	return runTTY(w.t, append([]string{"sh"}, argv...), runOpts(extra), *answer+"\n")
}

func ptr(s string) *string { return &s }

func (w *target) userHome() string { return base.TempDir(w.t) }

// pathWithoutLocalBin は PATH から、~/.local/bin を外した値。
func pathWithoutLocalBin() string {
	var kept []string
	for _, p := range filepath.SplitList(os.Getenv(env.Path)) {
		if !strings.Contains(filepath.ToSlash(p), ".local/bin") {
			kept = append(kept, p)
		}
	}
	return strings.Join(kept, string(os.PathListSeparator))
}

func (w *target) userSource(opts srcOpts) string {
	w.makeGit()
	return makeSource(w.t, opts)
}

func shareDir(home, machine string) string {
	return filepath.Join(home, ".local", "share", "ccnavi", machine)
}

// ターミナルでなければ断る。確認に自動で答えるオプションは無いので、エージェントは代わりに答えられない。
func TestBinUserWithoutATerminalWritesNothing(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	src := w.userSource(srcOpts{})
	home := w.userHome()
	r := w.userBin(home, nil, nil, "--deploy", src, "--bin", "user")
	code(t, r, 2)
	in(t, r.Stderr, "ターミナルでの確認が要ります")
	if len(shkit.Names(t, home)) != 0 || !reflect.DeepEqual(shkit.Names(t, w.dir), []string{".git"}) {
		t.Error("何かを書いた")
	}
}

func TestBinUserAsksOnTheTerminalAndPlacesTheBuildForThePath(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	src := w.userSource(srcOpts{})
	home := w.userHome()
	r := w.userBin(home, ptr("y"), map[string]string{env.Path: pathWithoutLocalBin()}, "--deploy", src, "--bin", "user")
	code(t, r, 0)
	share := shareDir(home, thisMachine())
	link := filepath.Join(home, ".local", "bin", "ccnavi")
	if !shkit.Exists(filepath.Join(share, "ccnavi")) {
		t.Error("実体を置いていない")
	}
	if to, err := os.Readlink(link); err != nil || to != filepath.Join(share, "ccnavi") {
		t.Errorf("リンク = %q, %v", to, err)
	}
	// 配布先の .ccnavi/bin/ には置かず、無視する行も追加しない。sh と設定は配る。
	if shkit.Exists(w.built(thisMachine())) {
		t.Error("配布先の bin/ に置いた")
	}
	if !shkit.Exists(w.launcher()) {
		t.Error("sh を配っていない")
	}
	if gi, _ := w.gitignore(); strings.Contains(gi, ".ccnavi/bin/") {
		t.Error(".gitignore に bin/ の行がある")
	}
	// リンクを置く場所が PATH に無ければ、追加する行を知らせる。シェルの設定ファイルには書かない。
	in(t, r.Stdout, "PATH に入っていません")
	if got := shkit.Names(t, home); !reflect.DeepEqual(got, []string{".local"}) {
		t.Errorf("ホームの中 = %v", got)
	}
	notIn(t, r.Stdout, "まだ無いもの")
}

func TestBinUserStopsWithoutWritingWhenTheAnswerIsNo(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	src := w.userSource(srcOpts{})
	home := w.userHome()
	r := w.userBin(home, ptr("n"), nil, "--deploy", src, "--bin", "user")
	code(t, r, 1)
	in(t, r.Stderr, "中止しました")
	if len(shkit.Names(t, home)) != 0 || !reflect.DeepEqual(shkit.Names(t, w.dir), []string{".git"}) {
		t.Error("何かを書いた")
	}
}

// 同じ中身なら確認なしで終わる。中身が違えば、またターミナルで確認する。
func TestBinUserAsksAgainOnlyWhenSomethingWillBeWritten(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	src := w.userSource(srcOpts{})
	home := w.userHome()
	args := []string{"--deploy", src, "--bin", "user"}
	code(t, w.userBin(home, ptr("y"), nil, args...), 0)

	again := w.userBin(home, nil, nil, args...)
	code(t, again, 0)
	notIn(t, again.Stdout, "続けますか")

	changeSourceBuild(t, src, "新しい組み立て")
	code(t, w.userBin(home, nil, nil, args...), 2)
	code(t, w.userBin(home, ptr("y"), nil, args...), 0)
	eq(t, "置き換えた実体", shkit.Read(t, filepath.Join(shareDir(home, thisMachine()), "ccnavi")), "新しい組み立て")
}

// ---- PATH の案内は、環境に合った追加の仕方を言う

// fakeUname は `uname -s` だけを system と答えるスタブのフォルダ。ほかの引数（`-m` など）は本物の uname に任せる。
func fakeUname(t testing.TB, real, system string) string {
	t.Helper()
	shim := base.TempDir(t)
	shkit.WriteExec(t, filepath.Join(shim, "uname"), "#!/bin/sh\ncase \"$1\" in -s) echo "+system+" ;; *) exec "+real+" \"$@\" ;; esac\n")
	return shim
}

// macOS の bash は ~/.bash_profile、Linux の bash は ~/.bashrc、zsh はどちらも ~/.zshrc。どの OS で回しても 4 通りを確かめるため、
// uname を差し替えて分岐を通す。fish は fish_add_path を案内し、rc ファイルには触れない。
func TestBinUserNamesTheRcFileOfTheShellInUse(t *testing.T) {
	t.Parallel()
	notWindows(t)
	haveCommands(t, "uname", "script")
	real := lookPath(t, "uname")
	cases := []struct {
		system, shell, rc string
	}{
		{"Linux", "/bin/bash", "~/.bashrc"},
		{"Linux", "/usr/bin/zsh", "~/.zshrc"},
		{"Darwin", "/bin/bash", "~/.bash_profile"},
		{"Darwin", "/usr/bin/zsh", "~/.zshrc"},
	}
	for _, c := range cases {
		t.Run(c.system+" "+c.shell, func(t *testing.T) {
			t.Parallel()
			w := newTarget(t)
			// 導入スクリプトは配布元の目印をこの機械の名前と比べるので、目印も system に合わせる。
			machine := strings.ToLower(c.system) + "-" + strings.SplitN(thisMachine(), "-", 2)[1]
			src := w.userSource(srcOpts{Target: machine})
			path := strings.Join([]string{fakeUname(t, real, c.system), pathWithoutLocalBin()}, string(os.PathListSeparator))
			r := w.userBin(w.userHome(), ptr("y"), map[string]string{"SHELL": c.shell, env.Path: path}, "--deploy", src, "--bin", "user")
			code(t, r, 0)
			in(t, r.Stdout, "PATH に入っていません", "export PATH=", ">> "+c.rc)
		})
	}
}

// 差し替えなしで、いま動いている OS に合った案内が出る。
func TestBinUserNamesTheRcFileThatFitsTheRealOS(t *testing.T) {
	t.Parallel()
	notWindows(t)
	haveCommands(t, "script")
	rc := "~/.bashrc"
	if runtime.GOOS == "darwin" {
		rc = "~/.bash_profile"
	}
	w := newTarget(t)
	src := w.userSource(srcOpts{})
	r := w.userBin(w.userHome(), ptr("y"), map[string]string{"SHELL": "/bin/bash", env.Path: pathWithoutLocalBin()}, "--deploy", src, "--bin", "user")
	code(t, r, 0)
	in(t, r.Stdout, ">> "+rc)
}

func TestBinUserNamesFishAddPathForFish(t *testing.T) {
	t.Parallel()
	notWindows(t)
	haveCommands(t, "script")
	w := newTarget(t)
	src := w.userSource(srcOpts{})
	r := w.userBin(w.userHome(), ptr("y"), map[string]string{"SHELL": "/usr/bin/fish", env.Path: pathWithoutLocalBin()}, "--deploy", src, "--bin", "user")
	code(t, r, 0)
	in(t, r.Stdout, "fish_add_path")
	notIn(t, r.Stdout, ">>")
}

func TestBinUserSaysNothingAboutThePathWhenItIsAlreadyThere(t *testing.T) {
	t.Parallel()
	notWindows(t)
	haveCommands(t, "script")
	w := newTarget(t)
	src := w.userSource(srcOpts{})
	home := w.userHome()
	onPath := strings.Join([]string{filepath.Join(home, ".local", "bin"), pathWithoutLocalBin()}, string(os.PathListSeparator))
	r := w.userBin(home, ptr("y"), map[string]string{env.Path: onPath}, "--deploy", src, "--bin", "user")
	code(t, r, 0)
	notIn(t, r.Stdout, "PATH に入っていません")
}

// Git Bash はリンクを使わず、ccnavi.exe が在るフォルダを PATH に通す。Windows の環境変数 Path に実体のフォルダを追加する手順を言い、
// rc には触れない。
func TestBinUserOnGitBashPutsTheFolderOnThePathAndMakesNoLink(t *testing.T) {
	t.Parallel()
	notWindows(t)
	haveCommands(t, "script")
	w := newTarget(t)
	src := w.userSource(srcOpts{Target: "windows-x86_64"})
	shkit.Write(t, filepath.Join(src, "dist", "ccnavi", "ccnavi.exe"), "exe")
	home := w.userHome()
	shim := base.TempDir(t)
	shkit.WriteExec(t, filepath.Join(shim, "uname"), "#!/bin/sh\ncase \"$1\" in -m) echo x86_64 ;; *) echo MINGW64_NT-10.0 ;; esac\n")
	path := strings.Join([]string{shim, pathWithoutLocalBin()}, string(os.PathListSeparator))
	r := w.userBin(home, ptr("y"), map[string]string{env.Path: path, "SHELL": "/usr/bin/bash"}, "--deploy", src, "--bin", "user")
	code(t, r, 0)
	share := shareDir(home, "windows-x86_64")
	if !shkit.Exists(filepath.Join(share, "ccnavi.exe")) {
		t.Error("exe を置いていない")
	}
	if shkit.Exists(filepath.Join(home, ".local", "bin")) {
		t.Error("リンクの置き場を作った")
	}
	in(t, r.Stdout, "SetEnvironmentVariable", share)
	notIn(t, r.Stdout, ">> ~/", "まだ無いもの")
}

// 配布先の bin/ に残った実行ファイルが先に使われる。使われなくなることを知らせる。
func TestBinUserWarnsWhenTheProjectStillHasItsOwnBuild(t *testing.T) {
	t.Parallel()
	notWindows(t)
	haveCommands(t, "script")
	w := newTarget(t)
	src := w.userSource(srcOpts{})
	w.setup("--deploy", src)
	r := w.userBin(w.userHome(), ptr("y"), nil, "--deploy", src, "--bin", "user")
	code(t, r, 0)
	in(t, r.Stdout, "実行ファイルが残っています", "先に使う")
}

// ~/.local/bin/ccnavi にディレクトリがあれば --force でも置き換えない。ユーザが自分で置いたものは --force が無ければ残す。
func TestBinUserKeepsWhatTheUserAlreadyHas(t *testing.T) {
	t.Parallel()
	notWindows(t)
	haveCommands(t, "script")
	t.Run("ディレクトリは --force でも置き換えない", func(t *testing.T) {
		t.Parallel()
		w := newTarget(t)
		src := w.userSource(srcOpts{})
		home := w.userHome()
		link := filepath.Join(home, ".local", "bin", "ccnavi")
		shkit.Mkdir(t, filepath.Join(link, "inside"))
		r := w.userBin(home, ptr("y"), nil, "--deploy", src, "--bin", "user", "--force")
		code(t, r, 0)
		if !shkit.Exists(filepath.Join(link, "inside")) || isLink(link) {
			t.Error("ディレクトリを置き換えた")
		}
		in(t, r.Stdout, "ディレクトリがあるので、リンクを張れない")
	})
	t.Run("自分で置いたファイルは --force が無ければ残す", func(t *testing.T) {
		t.Parallel()
		w := newTarget(t)
		src := w.userSource(srcOpts{})
		home := w.userHome()
		link := shkit.Write(t, filepath.Join(home, ".local", "bin", "ccnavi"), "自分で置いたもの")
		r := w.userBin(home, ptr("y"), nil, "--deploy", src, "--bin", "user")
		code(t, r, 0)
		if isLink(link) {
			t.Error("置き換えた")
		}
		in(t, r.Stdout, "配布先に既にあるので配っていないもの")
	})
}

func TestBinUserCheckWritesNothingAndDoesNotAsk(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	src := w.userSource(srcOpts{})
	home := w.userHome()
	r := w.userBin(home, nil, nil, "--deploy", src, "--bin", "user", "--check")
	code(t, r, 1)
	notIn(t, r.Stdout, "続けますか")
	if len(shkit.Names(t, home)) != 0 || !reflect.DeepEqual(shkit.Names(t, w.dir), []string{".git"}) {
		t.Error("何かを書いた")
	}
}

// 実行ファイルの置き場（--bin user）とは関係なく、ワークツリーの置き場は足す。
func TestBinUserStillAddsTheWorktreesLine(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	src := w.userSource(srcOpts{})
	r := w.setup("--deploy", src, "--check", "--bin", "user")
	if !listed(section(r.Stdout, ".gitignore に足す"), "/.claude/worktrees/") {
		t.Errorf("ワークツリーの行を足すと言っていない:\n%s", r.Stdout)
	}
}

// ---- 配る一覧と、配布元の状態

// 配る sh の一覧（setup が持つ DEPLOY_SCRIPTS）は、このツリーの .ccnavi/scripts/ の sh と同じ。
func TestTheDeployedListMatchesTheScriptsInTheTree(t *testing.T) {
	t.Parallel()
	found := regexp.MustCompile(`(?m)^DEPLOY_SCRIPTS="([^"]*)"$`).FindStringSubmatch(shkit.Read(t, scriptPath("ccnavi-setup.sh")))
	if found == nil {
		t.Fatal("DEPLOY_SCRIPTS が見つからない")
	}
	listed := map[string]bool{}
	for _, n := range strings.Fields(found[1]) {
		listed[n] = true
	}
	here := map[string]bool{}
	for _, n := range shkit.Names(t, base.ScriptsDir()) {
		if strings.HasSuffix(n, ".sh") || strings.HasSuffix(n, ".js") || strings.HasSuffix(n, ".txt") {
			here[n] = true
		}
	}
	if !reflect.DeepEqual(listed, here) {
		t.Errorf("配る一覧 = %v\nツリー = %v", listed, here)
	}
	want := map[string]bool{launcherName: true}
	for _, n := range deployScripts {
		want[n] = true
	}
	if !reflect.DeepEqual(listed, want) {
		t.Errorf("テストの一覧と食い違う: %v", listed)
	}
}

// 配布元に ccnavi-user-push-approved.sh が無ければ、最後の「まだ無いもの」に挙げる。ボードは承認のあとこの sh をターミナルに送る。
// 配れなかったことが最後の一覧に出ないと、配布先で承認の push ができない理由をユーザが読み取れない。
func TestDeployNamesThePushScriptWhenTheSourceLacksIt(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	src := makeSource(t, srcOpts{})
	shkit.Remove(t, filepath.Join(src, ".ccnavi", "scripts", "ccnavi-user-push-approved.sh"))
	r := w.setup("--mode", "enable", "--deploy", src)
	in(t, r.Stdout, "まだ無いもの")
	missing := strings.SplitN(r.Stdout, "まだ無いもの", 2)[1]
	in(t, missing, "ccnavi-user-push-approved.sh")
	if shkit.Exists(w.path(".ccnavi", "scripts", "ccnavi-user-push-approved.sh")) {
		t.Error("無い sh が配られた")
	}
	notIn(t, missing, "ccnavi-ticket.sh") // 他の sh は配ったので、一覧には出ない
}

// `--no-deploy` は配布物を置かない。何が無いかと、戻し方を見せる。--deploy と同時には指定できない（どちらを通すかをスクリプトの側で決めない）。
func TestDeployNoDeployWritesOnlyTheSettings(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	r := w.setup("--mode", "enable", "--no-deploy")
	code(t, r, 0)
	eq(t, "CCNAVI_MODE", w.envOf()["CCNAVI_MODE"], any("enable"))
	if shkit.Exists(w.path(".ccnavi", "bin")) {
		t.Error("bin/ を作った")
	}
	in(t, r.Stdout, "まだ無いもの", "--no-deploy を外すと")

	w2 := newTarget(t)
	r2 := w2.setup("--deploy", makeSource(t, srcOpts{}), "--no-deploy")
	code(t, r2, 2)
	in(t, r2.Stderr, "ccnavi-setup:")
	if shkit.Exists(w2.settingsPath()) {
		t.Error("設定を書いた")
	}
}

// 配布元を名指ししなくても配る。既定はスクリプト自身の置き場の 1 つ上。設定だけ書かれて実行ファイルが無い形は、hook が 8 つ登録されているのに
// 何も起動しない、という一番分かりにくい不具合になる。
func TestDeployCopiesWithoutBeingToldWhereFrom(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	src := makeSource(t, srcOpts{})
	r := w.copied(installScript(t, src), "--mode", "enable")
	code(t, r, 0)
	eq(t, "CCNAVI_MODE", w.envOf()["CCNAVI_MODE"], any("enable"))
	for _, p := range []string{w.built(thisMachine(), "ccnavi"), w.launcher(), w.rules()} {
		if !shkit.Exists(p) {
			t.Errorf("%s を配っていない", p)
		}
	}
	for _, name := range deployScripts {
		if !shkit.Exists(w.path(".ccnavi", "scripts", name)) {
			t.Errorf("%s を配っていない", name)
		}
	}
}

// 既定の配布元が組み立てられていなくても、設定は書く（名指しされていない配布元が空なのは打ったユーザの誤りではない。ここで断ると
// 組み立てていない機械では設定すら書けなくなる）。目印が無い既定の配布元も同じ。
func TestDeployWritesTheSettingsWhenTheDefaultSourceIsNotBuilt(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name string
		opts srcOpts
		say  string
	}{
		{"組み立てていない", srcOpts{Unbuilt: true}, "scripts/ccnavi-build.sh"},
		{"目印が無い", srcOpts{NoTarget: true}, ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			w := newTarget(t)
			r := w.copied(installScript(t, makeSource(t, c.opts)), "--mode", "enable")
			code(t, r, 0)
			eq(t, "CCNAVI_MODE", w.envOf()["CCNAVI_MODE"], any("enable"))
			in(t, r.Stdout, "配っていません", c.say)
			if shkit.Exists(w.path(".ccnavi", "bin")) {
				t.Error("実行ファイルを配った")
			}
		})
	}
}

// ccnavi 自身に打っても止まらない。配る先が無いので、配布だけ諦める。
func TestDeployDoesNotRefuseWhenTheDefaultSourceIsTheTarget(t *testing.T) {
	t.Parallel()
	haveCommands(t, "sh", "jq")
	src := makeSource(t, srcOpts{})
	script := installScript(t, src)
	r := runCmdArgs(t, script, src, "--mode", "enable")
	code(t, r, 0)
	in(t, r.Stdout, "配布元と配布先が同じ")
	eq(t, "CCNAVI_MODE", parseJSON(t, shkit.Read(t, filepath.Join(src, ".claude", "settings.json")))["env"].(map[string]any)["CCNAVI_MODE"], any("enable"))
}

// 実行の許しを外さない。外れていると hook は「実行ファイルが無い」ではなく「起動できない」で何も言わずに失敗する。
func TestDeployTheExecutableCanStillBeRun(t *testing.T) {
	t.Parallel()
	notWindows(t)
	w := newTarget(t)
	w.setup("--deploy", makeSource(t, srcOpts{}))
	if !canExec(w.built(thisMachine(), "ccnavi")) || !canExec(w.launcher()) {
		t.Error("実行ビットが付いていない")
	}
}

// 2 回目は配らない。既にあるものとして並べる。
func TestDeployRunningTwiceDoesNotCopyAgain(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	src := makeSource(t, srcOpts{})
	w.setup("--deploy", src)
	shkit.Write(t, w.rules(), "# このプロジェクトで直したルール\n")
	r := w.setup("--deploy", src)
	code(t, r, 0)
	in(t, r.Stdout, "配布先に既にあるので配っていないもの")
	in(t, shkit.Read(t, w.rules()), "このプロジェクトで直した")
}

// 組み立て直して中身が違えば、`--force` が無くても実行ファイルを置き換える（古いままだと、sh との互換の食い違いで hook が働かなくなる）。
// 前の組み立ての残りも持ち越さず、新しい版を隣に作ってから入れ替えるので途中の置き場を残さない。置き換えるのは実行ファイルだけで、
// 直されている前提のルールには触らない。中身が同じなら置き換えない。--check は違う実行ファイルを「揃っていない」に数え、書かない。
func TestDeployReplacesABuildThatDiffersFromTheSource(t *testing.T) {
	t.Parallel()
	t.Run("force が無くても置き換え、前の版の残りを持ち越さない", func(t *testing.T) {
		t.Parallel()
		w := newTarget(t)
		src := makeSource(t, srcOpts{})
		w.setup("--deploy", src)
		stale := shkit.Write(t, w.built(thisMachine(), "前の版の残り"), "前の版")
		shkit.Write(t, w.rules(), "# このプロジェクトで直したルール\n")
		changeSourceBuild(t, src, "新しい組み立て")
		r := w.setup("--deploy", src)
		code(t, r, 0)
		in(t, r.Stdout, "入れ替えた")
		eq(t, "実行ファイル", shkit.Read(t, w.built(thisMachine(), "ccnavi")), "新しい組み立て")
		if shkit.Exists(stale) {
			t.Error("前の版の残りが残った")
		}
		for _, n := range shkit.Names(t, filepath.Dir(w.built(thisMachine()))) {
			if strings.Contains(n, ".new.") || strings.Contains(n, ".old.") {
				t.Errorf("途中の置き場が残った: %s", n)
			}
		}
		in(t, shkit.Read(t, w.rules()), "このプロジェクトで直した")
	})
	t.Run("中身が同じなら置き換えない", func(t *testing.T) {
		t.Parallel()
		w := newTarget(t)
		src := makeSource(t, srcOpts{})
		w.setup("--deploy", src)
		before, _ := os.Stat(w.built(thisMachine(), "ccnavi"))
		r := w.setup("--deploy", src)
		code(t, r, 0)
		notIn(t, r.Stdout, "入れ替えた")
		after, _ := os.Stat(w.built(thisMachine(), "ccnavi"))
		if !before.ModTime().Equal(after.ModTime()) {
			t.Error("置き換えた")
		}
	})
	t.Run("--check は違う実行ファイルを揃っていないに数え、書かない", func(t *testing.T) {
		t.Parallel()
		w := newTarget(t)
		src := makeSource(t, srcOpts{})
		w.setup("--deploy", src)
		changeSourceBuild(t, src, "新しい組み立て")
		code(t, w.setup("--deploy", src, "--check"), 1)
		eq(t, "実行ファイル", shkit.Read(t, w.built(thisMachine(), "ccnavi")), "#!/bin/sh\nexit 0\n")
	})
}

// `--force` なら入れ替える。前の組み立ての残りも持ち越さない（前の版にだけあったファイルが残ると、置き場の中身がどの版のものか分からなくなる）。
func TestDeployForceReplacesAndDropsThePreviousBuild(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	src := makeSource(t, srcOpts{})
	w.setup("--deploy", src)
	stale := shkit.Write(t, w.built(thisMachine(), "前の版の残り"), "前の版")
	shkit.Write(t, w.rules(), "# 直したルール\n")
	r := w.setup("--deploy", src, "--force")
	code(t, r, 0)
	in(t, r.Stdout, "入れ替えた")
	if shkit.Exists(stale) {
		t.Error("前の版の残りが残った")
	}
	eq(t, "ルール", shkit.Read(t, w.rules()), "deny: []\n")
}

// 組み立てていない配布元では、何も言わずに進まない（報告だけにすると「配ったはずなのに実行ファイルが無い」が最後の一覧にしか出ない）。
func TestDeployRefusesASourceThatWasNeverBuilt(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	r := w.setup("--deploy", makeSource(t, srcOpts{Unbuilt: true}))
	code(t, r, 2)
	in(t, r.Stderr, "scripts/ccnavi-build.sh")
	if shkit.Exists(w.settingsPath()) {
		t.Error("設定を書いた")
	}
}

// 配布元に無いものは、何も言わずに飛ばすのではなく名前を挙げる。
func TestDeployNamesWhatTheSourceDoesNotHave(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	r := w.setup("--deploy", makeSource(t, srcOpts{NoParts: true}))
	code(t, r, 0)
	in(t, r.Stdout, "配布元に無くて配れないもの", "rules.yml", "ccnavi-launcher.sh")
	if !shkit.Exists(w.built(thisMachine(), "ccnavi")) {
		t.Error("実行ファイルは配るはず")
	}
}

// 配布元の .ccnavi/scripts/ に sh が無ければ、配れないものに挙げる。以前の原本の置き場（scripts/ccnavi-launcher.sh）に在っても、
// そこからは配らない（拾うと、配布元の版が混ざったまま「配った」に見える）。
func TestDeployNamesTheLauncherTheSourceDoesNotHave(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	src := makeSource(t, srcOpts{NoLauncher: true})
	copyFile(t, launcherPath(), filepath.Join(src, "scripts", launcherName), 0o644)
	r := w.setup("--deploy", src)
	code(t, r, 0)
	wantListed(t, r.Stdout, "配布元に無くて配れないもの", binPath)
	if shkit.Exists(w.launcher()) {
		t.Error("別の置き場の sh を配った")
	}
	wantListed(t, r.Stdout, "まだ無いもの", binPath)
}

// 配布先の sh の実行ビットが外れていれば、配らない回でも付け直す。sh は追跡するので、Windows で足した sh は 100644 で入り、別の機械で
// clone した直後は実行ビットが無い。触らずにいると、打ち直しても hook は 126 で起動しないままになる。中身は入れ替えない。
func TestDeployPutsTheRunBitBackOnALauncherItKeeps(t *testing.T) {
	t.Parallel()
	notWindows(t)
	w := newTarget(t)
	src := makeSource(t, srcOpts{})
	w.setup("--deploy", src)
	shkit.Write(t, w.launcher(), "#!/bin/sh\n# このプロジェクトで直した sh\n")
	chmod(t, w.launcher(), 0o644)
	r := w.setup("--deploy", src)
	code(t, r, 0)
	if !canExec(w.launcher()) {
		t.Error("実行ビットを付け直していない")
	}
	in(t, shkit.Read(t, w.launcher()), "このプロジェクトで直した")
}

// `--check` は配らない。揃っていないので 1 を返し、揃えば 0 を返す。
func TestDeployCheck(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	src := makeSource(t, srcOpts{})
	r := w.setup("--deploy", src, "--check")
	code(t, r, 1)
	in(t, r.Stdout, "配る")
	if shkit.Exists(w.path(".ccnavi", "bin")) || shkit.Exists(w.launcher()) {
		t.Error("--check が配った")
	}
	w.setup("--deploy", src)
	code(t, w.setup("--deploy", src, "--check"), 0)
}

func TestDeployRefusesBadSources(t *testing.T) {
	t.Parallel()
	src := makeSource(t, srcOpts{})
	t.Run("配布元と配布先が同じ", func(t *testing.T) {
		t.Parallel()
		r := newTarget(t).raw(src, "--deploy", src)
		code(t, r, 2)
		in(t, r.Stderr, "ccnavi-setup:")
	})
	t.Run("配布元が無い", func(t *testing.T) {
		t.Parallel()
		w := newTarget(t)
		code(t, w.setup("--deploy", w.path("どこにもない")), 2)
	})
	t.Run("値が無い", func(t *testing.T) {
		t.Parallel()
		code(t, newTarget(t).setup("--deploy"), 2)
	})
}

// ---- 本物の skills・docs・CCNAVI.md を配布元に置いて配った結果を読む。配るのは 1 回。

func TestDeployTheRealTree(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	root := base.Root()
	src := makeSource(t, srcOpts{NoDocs: true})
	shkit.Remove(t, filepath.Join(src, ".claude", "skills"))
	shkit.CopyTree(t, filepath.Join(root, ".claude", "skills"), filepath.Join(src, ".claude", "skills"))
	shkit.CopyTree(t, filepath.Join(root, ".ccnavi", "docs"), filepath.Join(src, ".ccnavi", "docs"))
	copyFile(t, base.Final(".ccnavi/CCNAVI.md"), filepath.Join(src, ".ccnavi", "CCNAVI.md"), 0o644)
	r := w.setup("--mode", "enable", "--force", "--deploy", src)
	code(t, r, 0)

	t.Run("本物の ccnavi-config のスキルが届き、配布元固有のスキルは配らない", func(t *testing.T) {
		skill := w.path(".claude", "skills", "ccnavi-config")
		for _, rel := range []string{"SKILL.md", "references/common/add.md", "references/common/check.md"} {
			if !shkit.Exists(filepath.Join(skill, filepath.FromSlash(rel))) {
				t.Errorf("%s が届いていない", rel)
			}
		}
		for _, name := range []string{"commit", "yomiyasu", "eli5", "ccnavi-install"} {
			if shkit.Exists(w.path(".claude", "skills", name)) {
				t.Errorf("配布元固有の %s を配った", name)
			}
		}
	})

	// 配布先の文書が案内する `/<スキル名>` と `.claude/skills/...` が、配布先に実在する。
	t.Run("配った文書は配布先にあるものだけを指す", func(t *testing.T) {
		deployed := map[string]bool{}
		for _, n := range shkit.Names(t, w.path(".claude", "skills")) {
			deployed[n] = true
		}
		docs := []string{w.path(".ccnavi", "CCNAVI.md")}
		for _, n := range shkit.Names(t, w.path(".ccnavi", "docs")) {
			if strings.HasSuffix(n, ".md") {
				docs = append(docs, w.path(".ccnavi", "docs", n))
			}
		}
		// 配布元の .claude/skills にあるスキル名を、配布先にも無ければならない名前として拾う（組み込みのコマンドは拾わない）。
		source := map[string]bool{}
		for _, n := range shkit.Names(t, filepath.Join(root, ".claude", "skills")) {
			source[n] = true
		}
		slash := regexp.MustCompile("`/([a-z][a-z0-9-]*)(?:\\s[^`]*)?`")
		ref := regexp.MustCompile(`\.claude/skills/[A-Za-z0-9._/\-]+`)
		for _, path := range docs {
			text := shkit.Read(t, path)
			rel, _ := filepath.Rel(w.dir, path)
			for _, m := range slash.FindAllStringSubmatch(text, -1) {
				if source[m[1]] && !deployed[m[1]] {
					t.Errorf("%s: /%s は配布先に無い", rel, m[1])
				}
			}
			for _, loc := range ref.FindAllStringIndex(text, -1) {
				target := strings.TrimRight(text[loc[0]:loc[1]], "./-")
				if loc[1] < len(text) && text[loc[1]] == '*' {
					continue // glob（範囲の例）は場所の案内ではない
				}
				if !shkit.Exists(w.path(strings.Split(target, "/")...)) {
					t.Errorf("%s: %s は配布先に無い", rel, target)
				}
			}
		}
	})

	// 配るスキルの本文が、配布元にしか無いパスや `uv run` を指さない。
	t.Run("配ったスキルは配布元にしか無いものを指さない", func(t *testing.T) {
		forbidden := map[string]*regexp.Regexp{
			"uv run":     regexp.MustCompile(`\buv run\b`),
			"配布元の docs/": regexp.MustCompile(`(?:^|[^\w])(?:docs/\d\d_|\.agents/docs)`),
			"配布元の go/":   regexp.MustCompile(`(?:^|[^\w/])go/(?:internal|cmd)/`),
			"親ディレクトリをさかのぼる": regexp.MustCompile(`\]\((?:\.\./)+`),
		}
		link := regexp.MustCompile(`\]\((references/[^)#]+)\)`)
		skills := w.path(".claude", "skills")
		for _, rel := range listing(t, skills) {
			path := filepath.Join(skills, filepath.FromSlash(rel))
			text := shkit.Read(t, path)
			for label, re := range forbidden {
				if re.MatchString(text) {
					t.Errorf("%s: %s", rel, label)
				}
			}
			// 同じスキルの中を指すリンクは、配布先で辿れる。
			if filepath.Base(path) == "SKILL.md" {
				for _, m := range link.FindAllStringSubmatch(text, -1) {
					if !shkit.Exists(filepath.Join(filepath.Dir(path), filepath.FromSlash(m[1]))) {
						t.Errorf("%s: %s は配布先に無い", rel, m[1])
					}
				}
			}
		}
	})
}

// ---- 実行ファイルを、組み立てた機械の置き場（`<os>-<arch>/`）に入れる。

// 実行ファイルは組み立てた向きの OS と CPU でしか動かない。機械ごとに置き場を分けるので、別の機械向けを配ってもこの機械の
// 実行ファイルは上書きされない。ただしこの機械で動くものが無いことは、省かずに言う。
func TestDeployPlacesABuildForAnotherMachineInItsOwnPlace(t *testing.T) {
	t.Parallel()
	t.Run("名指しした配布元", func(t *testing.T) {
		t.Parallel()
		w := newTarget(t)
		r := w.setup("--deploy", makeSource(t, srcOpts{Target: anotherMachine}))
		code(t, r, 0)
		if !shkit.Exists(w.built(anotherMachine, "ccnavi")) || shkit.Exists(w.built(thisMachine())) {
			t.Error("置き場が違う")
		}
		in(t, r.Stdout, anotherMachine+" 向け", "まだ無いもの", thisMachine()+"/ccnavi")
	})
	t.Run("既定の配布元", func(t *testing.T) {
		t.Parallel()
		w := newTarget(t)
		r := w.copied(installScript(t, makeSource(t, srcOpts{Target: anotherMachine})))
		code(t, r, 0)
		if !shkit.Exists(w.built(anotherMachine, "ccnavi")) || !shkit.Exists(w.settingsPath()) {
			t.Error("置き場が違う")
		}
	})
}

// Windows と WSL で同じフォルダを開く形。片方を配っても、もう片方は残る。
func TestDeployKeepsBuildsForTwoMachinesSideBySide(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	w.setup("--deploy", makeSource(t, srcOpts{Target: anotherMachine}))
	r := w.setup("--deploy", makeSource(t, srcOpts{}))
	code(t, r, 0)
	if !shkit.Exists(w.built(anotherMachine, "ccnavi")) || !shkit.Exists(w.built(thisMachine(), "ccnavi")) {
		t.Error("片方が残っていない")
	}
	notIn(t, r.Stdout, "まだ無いもの")
}

func TestDeployABuildForThisMachineCarriesNoNoteAndNoMark(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	r := w.setup("--deploy", makeSource(t, srcOpts{}))
	code(t, r, 0)
	if !shkit.Exists(w.built(thisMachine(), "ccnavi")) {
		t.Error("実行ファイルを配っていない")
	}
	notIn(t, r.Stdout, "確かめていません", "向けで")
	// 目印は dist/ccnavi/ の外にあるので、配布先へはコピーされない。
	for _, parts := range [][]string{{".ccnavi", "ccnavi.target"}, {".ccnavi", "bin", "ccnavi.target"}, {".ccnavi", "bin", thisMachine(), "ccnavi.target"}} {
		if shkit.Exists(w.path(parts...)) {
			t.Errorf("目印を配った: %v", parts)
		}
	}
}

// 目印が無いと置き場を決められない（推測で置くと、別の機械向けを入れうる）。名指しした配布元は 2 で断り、既定の配布元は設定だけ書く。
func TestDeployASourceWithoutTheMark(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	r := w.setup("--deploy", makeSource(t, srcOpts{NoTarget: true}))
	code(t, r, 2)
	in(t, r.Stderr, "ccnavi.target")
	if shkit.Exists(w.settingsPath()) {
		t.Error("設定を書いた")
	}
}

// 目印はそのままディレクトリ名になる。区切りを含む値で置き場の外へ書かせない。
func TestDeployRefusesAMarkThatWouldLeaveThePlace(t *testing.T) {
	t.Parallel()
	for _, mark := range []string{"../escape", "linux/x86_64", "linux"} {
		w := newTarget(t)
		r := w.setup("--deploy", makeSource(t, srcOpts{Target: mark}))
		code(t, r, 2)
		if shkit.Exists(w.path(".ccnavi", "escape")) || shkit.Exists(w.path("escape")) {
			t.Errorf("%s: 置き場の外へ書いた", mark)
		}
	}
}

// ---- 既定でない CCNAVI_ENTRY_PATH（ユーザが決めたパス）は書き換えず、名指しする。

// ユーザが決めたパスの先で何が使われているかを、このスクリプトは決められない。案内は settings.json の値の直し方。終了コードは 0。
// --check では「揃っていない」に数える。
func TestSetupLeavesAPathItDidNotWriteAndNamesIt(t *testing.T) {
	t.Parallel()
	const custom = "dist/ccnavi/ccnavi"
	t.Run("書き換えず名指しする", func(t *testing.T) {
		t.Parallel()
		w := newTarget(t)
		w.writeSettings(jsonObj{"env": jsonObj{"CCNAVI_ENTRY_PATH": custom}})
		r := w.setup("--deploy", makeSource(t, srcOpts{}))
		code(t, r, 0)
		eq(t, "CCNAVI_ENTRY_PATH", w.envOf()["CCNAVI_ENTRY_PATH"], any(custom))
		named := false
		for _, line := range strings.Split(r.Stdout, "\n") {
			named = named || (strings.Contains(line, custom) && strings.Contains(line, binPath))
		}
		if !named {
			t.Errorf("名指しする行が無い:\n%s", r.Stdout)
		}
		if !shkit.Exists(w.built(thisMachine(), "ccnavi")) {
			t.Error("実行ファイルは配るはず")
		}
	})
	t.Run("--check は揃っていないに数える", func(t *testing.T) {
		t.Parallel()
		w := newTarget(t)
		src := makeSource(t, srcOpts{})
		w.setup("--deploy", src)
		code(t, w.setup("--deploy", src, "--check"), 0)
		data := w.settings()
		data["env"].(map[string]any)["CCNAVI_ENTRY_PATH"] = custom
		w.writeSettings(data)
		r := w.setup("--deploy", src, "--check")
		code(t, r, 1)
		in(t, r.Stdout, custom)
		eq(t, "CCNAVI_ENTRY_PATH", w.envOf()["CCNAVI_ENTRY_PATH"], any(custom))
	})
}
