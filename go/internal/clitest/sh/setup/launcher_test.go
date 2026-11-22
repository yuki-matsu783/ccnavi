package setup

import (
	"fmt"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/shkit"
	"ccnavi/internal/infra/env"
)

// 振り分けの sh（.ccnavi/scripts/ccnavi-launcher.sh）。sh は `.ccnavi/scripts/` に、実体は `.ccnavi/bin/<os>-<arch>/` に並ぶ。
// 語を読む場所は 3 つ（sh、go/internal/infra/platformtag、ccnavi-setup.sh）あり、どれかだけ変わると配った場所と探す場所が食い違う。
// ここでは sh を外から動かし、選んだ置き場を見る。機械を差し替えるときは、PATH の先頭に偽の uname を置く。

type launcherEnv struct {
	t                           *testing.T
	dir, scripts, bin, launcher string
	fake                        string
}

func newLauncher(t *testing.T) *launcherEnv {
	t.Helper()
	notWindows(t)
	haveCommands(t, "sh")
	l := &launcherEnv{t: t, dir: base.TempDir(t)}
	l.scripts = filepath.Join(l.dir, ".ccnavi", "scripts")
	l.bin = filepath.Join(l.dir, ".ccnavi", "bin")
	l.launcher = filepath.Join(l.scripts, "ccnavi-launcher.sh")
	copyFile(t, launcherPath(), l.launcher, 0o755)
	l.fake = filepath.Join(l.dir, "fake-path")
	return l
}

// build は組み立ての代わり。どの置き場から起動されたかと、引数と標準入力を返す。place が空なら `.ccnavi/bin/`。終了コードは code。
func (l *launcherEnv) build(target string) { l.put("", target, "ccnavi", 0) }

func (l *launcherEnv) put(place, target, name string, code int) {
	where := "bin"
	if place == "" {
		place = l.bin
	} else {
		where = filepath.Base(place)
	}
	shkit.WriteExec(l.t, filepath.Join(place, target, name),
		fmt.Sprintf("#!/bin/sh\nprintf \"%%s|%%s|%%s\" \"%s/%s/%s\" \"$*\" \"$(cat)\"\nexit %d\n", where, target, name, code))
}

func (l *launcherEnv) pretend(uname string) {
	shkit.WriteExec(l.t, filepath.Join(l.fake, "uname"), fmt.Sprintf("#!/bin/sh\necho %q\n", uname))
}

type launch struct {
	args     []string
	noStdin  bool // 標準入力を渡さない（既定は "{}"）
	stdin    string
	realUser bool // 偽の uname を使わない
	argv     []string
	dir      string
	pathDirs []string
}

func (l *launcherEnv) run(o launch) run.Result {
	l.t.Helper()
	path := pathWith()
	if !o.realUser {
		path = pathWith(l.fake)
	}
	if len(o.pathDirs) > 0 {
		path = strings.Join(append(append([]string{}, o.pathDirs...), path), string(filepath.ListSeparator))
	}
	stdin := o.stdin
	if stdin == "" && !o.noStdin {
		stdin = "{}"
	}
	opts := run.Opts{Input: stdin, Dir: o.dir, Env: map[string]string{env.Path: path}}
	if o.argv != nil {
		return run.Command(l.t, o.argv[0], append(o.argv[1:], o.args...), opts)
	}
	return run.Command(l.t, l.launcher, o.args, opts)
}

func startsWith(t testing.TB, got, prefix string) {
	t.Helper()
	if !strings.HasPrefix(got, prefix) {
		t.Errorf("%q で始まらない: %q", prefix, got)
	}
}

func TestLauncherStartsTheBuildForThisMachineInTheSameWordsAsTheExecutable(t *testing.T) {
	t.Parallel()
	l := newLauncher(t)
	l.build(exe.HostTarget())
	l.build("haiku-riscv64")
	r := l.run(launch{realUser: true})
	code(t, r, 0)
	startsWith(t, r.Stdout, "bin/"+exe.HostTarget()+"/")
}

func TestLauncherPassesArgumentsAndThePayloadThrough(t *testing.T) {
	t.Parallel()
	l := newLauncher(t)
	l.pretend("Linux x86_64")
	l.build("linux-x86_64")
	r := l.run(launch{args: []string{"--root", "/a b", "ticket"}, stdin: `{"hook_event_name":"Stop"}`})
	code(t, r, 0)
	eq(t, "stdout", r.Stdout, `bin/linux-x86_64/ccnavi|--root /a b ticket|{"hook_event_name":"Stop"}`)
}

// hook は実体の終了コードで判定を読む（2 は拒否）。sh が書き換えない。
func TestLauncherPassesTheExitCodeThrough(t *testing.T) {
	t.Parallel()
	l := newLauncher(t)
	l.pretend("Linux x86_64")
	l.put("", "linux-x86_64", "ccnavi", 2)
	r := l.run(launch{})
	code(t, r, 2)
	startsWith(t, r.Stdout, "bin/linux-x86_64/")
}

// `$0` にディレクトリの部分が無い（`here=.`）ときも、1 つ上の `bin/` を探す。
func TestLauncherFindsTheBinFromRelativeSpellings(t *testing.T) {
	t.Parallel()
	l := newLauncher(t)
	l.pretend("Linux x86_64")
	l.build("linux-x86_64")
	cases := []struct {
		name string
		argv []string
		dir  string
	}{
		{"ワークスペースルートから", []string{"sh", filepath.Join(".ccnavi", "scripts", "ccnavi-launcher.sh")}, l.dir},
		{"sh の置き場から", []string{"sh", "ccnavi-launcher.sh"}, l.scripts},
	}
	for _, c := range cases {
		r := l.run(launch{args: []string{"x"}, argv: c.argv, dir: c.dir})
		code(t, r, 0)
		startsWith(t, r.Stdout, "bin/linux-x86_64/ccnavi|x|")
	}
}

// ワークツリーの中の launcher には bin/ が無い。git の共通ディレクトリから本体のルートの bin/ を使う。
func TestLauncherInAWorktreeWithoutBinStartsTheBuildInTheMainRoot(t *testing.T) {
	t.Parallel()
	haveCommands(t, "git")
	l := newLauncher(t)
	l.pretend("Linux x86_64")
	l.build("linux-x86_64")
	gitInit(t, l.dir)
	run.Git(t, l.dir, "add", ".ccnavi/scripts")
	run.Git(t, l.dir, "commit", "-q", "-m", "i")
	run.Git(t, l.dir, "worktree", "add", "-q", filepath.Join(l.dir, "wt"))
	if shkit.Exists(filepath.Join(l.dir, "wt", ".ccnavi", "bin")) {
		t.Fatal("ワークツリーに bin/ がある")
	}
	r := l.run(launch{args: []string{"x"}, argv: []string{"sh", filepath.Join(l.dir, "wt", ".ccnavi", "scripts", "ccnavi-launcher.sh")}})
	code(t, r, 0)
	startsWith(t, r.Stdout, "bin/linux-x86_64/ccnavi|x|")
}

// 本体のルートにも実体が無いときは、本体の launcher に渡し直しても戻らず 127 で終わる。
func TestLauncherWithNoBuildAnywhereStillEndsWith127(t *testing.T) {
	t.Parallel()
	l := newLauncher(t)
	l.pretend("Linux x86_64")
	r := l.run(launch{argv: []string{"sh", l.launcher}})
	code(t, r, 127)
}

// 機械ごとに選ぶ置き場。arm64 は自分の組み立てを先に、無ければ翻訳で動く x86_64 を使う（Linux は使わない）。
func TestLauncherChoosesTheBuildForTheMachine(t *testing.T) {
	t.Parallel()
	type build struct{ target, name string }
	cases := []struct {
		name   string
		uname  string
		builds []build
		want   string // 起動された置き場（空なら 127 で何も起動しない）
	}{
		{"Git Bash は exe を起動する", "MINGW64_NT-10.0-26100 x86_64", []build{{"windows-x86_64", "ccnavi.exe"}, {"linux-x86_64", "ccnavi"}}, "bin/windows-x86_64/ccnavi.exe|"},
		{"arm64 の mac は自分の組み立てを先に使う", "Darwin arm64", []build{{"darwin-x86_64", "ccnavi"}, {"darwin-arm64", "ccnavi"}}, "bin/darwin-arm64/"},
		{"arm64 の mac は翻訳の組み立てに落ちる", "Darwin arm64", []build{{"darwin-x86_64", "ccnavi"}}, "bin/darwin-x86_64/"},
		{"arm64 の Windows は自分の組み立てを先に使う", "MINGW64_NT-10.0-26100 arm64", []build{{"windows-x86_64", "ccnavi.exe"}, {"windows-arm64", "ccnavi.exe"}}, "bin/windows-arm64/ccnavi.exe|"},
		{"arm64 の Windows は翻訳の組み立てに落ちる", "MINGW64_NT-10.0-26100 arm64", []build{{"windows-x86_64", "ccnavi.exe"}}, "bin/windows-x86_64/ccnavi.exe|"},
		{"arm64 の Linux は落ちない", "Linux aarch64", []build{{"linux-x86_64", "ccnavi"}}, ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			l := newLauncher(t)
			l.pretend(c.uname)
			for _, b := range c.builds {
				l.put("", b.target, b.name, 0)
			}
			r := l.run(launch{})
			if c.want == "" {
				code(t, r, 127)
				eq(t, "stdout", r.Stdout, "")
				return
			}
			code(t, r, 0)
			startsWith(t, r.Stdout, c.want)
		})
	}
}

// 隣（`.ccnavi/scripts/<os>-<arch>/`）は配る場所ではない。自己防衛のパスがヒットしない。bin/ があればそちらを使う。
func TestLauncherDoesNotStartABuildPlacedNextToItself(t *testing.T) {
	t.Parallel()
	for _, withBin := range []bool{false, true} {
		l := newLauncher(t)
		l.pretend("Linux x86_64")
		l.put(l.scripts, "linux-x86_64", "ccnavi", 0)
		if withBin {
			l.build("linux-x86_64")
		}
		r := l.run(launch{})
		if !withBin {
			code(t, r, 127)
			eq(t, "stdout", r.Stdout, "")
			continue
		}
		code(t, r, 0)
		startsWith(t, r.Stdout, "bin/linux-x86_64/")
	}
}

// 実行ファイルそのものが無いときにシェルが返すのと同じ 127 で終わり、どの機械で・どこを探したかを言う。
// 探した場所は `..` を含むまま出る（正規化しない）。
func TestLauncherSaysWhichMachineHasNoBuildAndWhereItLooked(t *testing.T) {
	t.Parallel()
	cases := []struct{ name, uname, builds, want string }{
		{"実体が別の機械向け", "Linux x86_64", "darwin-arm64", "linux-x86_64"},
		{"uname が何も言わない", "Haiku riscv64", "linux-x86_64", "unknown-unknown"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			l := newLauncher(t)
			l.pretend(c.uname)
			l.build(c.builds)
			r := l.run(launch{})
			code(t, r, 127)
			in(t, r.Stderr, c.want, l.scripts+"/../bin/")
			eq(t, "stdout", r.Stdout, "")
		})
	}
}

// bin/ に無いときだけ PATH の ccnavi を使う。
func (l *launcherEnv) onPath(name, where string) string {
	place := filepath.Join(l.dir, "on-path")
	shkit.WriteExec(l.t, filepath.Join(place, name), fmt.Sprintf("#!/bin/sh\nprintf \"%%s|%%s\" \"%s\" \"$*\"\n", where))
	return place
}

func TestLauncherFallsBackToTheCcnaviOnPathOnlyWhenBinHasNoBuild(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name      string
		withBuild bool
		file      string
		want      string
	}{
		{"bin に無ければ PATH の ccnavi", false, "ccnavi", "from-path|x"},
		{"bin が先", true, "ccnavi", "bin/linux-x86_64/"},
		{"exe の綴りも探す", false, "ccnavi.exe", "from-path|x"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			l := newLauncher(t)
			l.pretend("Linux x86_64")
			if c.withBuild {
				l.build("linux-x86_64")
			}
			place := l.onPath(c.file, "from-path")
			r := l.run(launch{args: []string{"x"}, pathDirs: []string{place}})
			code(t, r, 0)
			startsWith(t, r.Stdout, c.want)
		})
	}
}

// cwd に置かれた ccnavi を拾わない。相対の項目と空の項目は結果が cwd で変わる。
func TestLauncherSkipsRelativeAndEmptyPathEntries(t *testing.T) {
	t.Parallel()
	l := newLauncher(t)
	l.pretend("Linux x86_64")
	shkit.WriteExec(t, filepath.Join(l.dir, "evil", "ccnavi"), "#!/bin/sh\nprintf \"evil\"\n")
	for _, entry := range []string{"evil", "", "."} {
		r := l.run(launch{pathDirs: []string{entry}, dir: l.dir})
		code(t, r, 127)
		eq(t, "stdout", r.Stdout, "")
	}
}

// ccnavi という名前で張ったリンクを起動すると自分に戻る。同じファイルは飛ばす。
func TestLauncherSkipsACcnaviThatIsTheLauncherItself(t *testing.T) {
	t.Parallel()
	if !base.CanSymlink() {
		t.Skip("シンボリックリンクを作れない")
	}
	l := newLauncher(t)
	l.pretend("Linux x86_64")
	place := filepath.Join(l.dir, "on-path")
	symlink(t, l.launcher, filepath.Join(place, "ccnavi"))
	r := run.Command(t, filepath.Join(place, "ccnavi"), nil,
		run.Opts{Input: "{}", Env: map[string]string{env.Path: pathWith(l.fake, place)}})
	code(t, r, 127)
}

func TestLauncherNo127MessageForgetsPath(t *testing.T) {
	t.Parallel()
	l := newLauncher(t)
	l.pretend("Linux x86_64")
	r := l.run(launch{})
	code(t, r, 127)
	in(t, r.Stderr, "PATH")
}

// PATH の探索で IFS と set -f を変える。空白を含む PATH の項目でも探せる（項目は : だけで分ける）。
func TestLauncherFindsACcnaviInAPathEntryWithASpace(t *testing.T) {
	t.Parallel()
	l := newLauncher(t)
	l.pretend("Linux x86_64")
	spaced := filepath.Join(l.dir, "a b")
	shkit.WriteExec(t, filepath.Join(spaced, "ccnavi"), "#!/bin/sh\nprintf \"spaced\"\n")
	r := l.run(launch{pathDirs: []string{spaced}})
	eq(t, "stdout", r.Stdout, "spaced")
}
