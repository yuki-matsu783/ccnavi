package base

import (
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"

	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
)

// waitingParallel は、clitest のテストの並行の数の既定（-parallel を渡せばそちらが優先）。CPU の数（GOMAXPROCS）の 2 倍。
// このテストは、子のプロセス（ccnavi・git・sh）の終わりを待つ時間が長いので、go test の既定（CPU の数）では待ちが重なり切らない。
// 一方で子の多くは CPU を使うので、固定の大きな数（16 を測った）にすると、4 CPU では 8 より速くならず、sh/git のような重いパッケージは
// 4 より遅くなり、ほかの go test と重なったときに時間の上限で落ちる。
// 4 CPU での測定（2026-11）: union は 4 から 8 で約 3 割速い、8 と 16 は同等、sh/git は 4 が最速。
func waitingParallel() string { return strconv.Itoa(2 * runtime.GOMAXPROCS(0)) }

// atExit は Main が終わりに消す置き場を覚える。
func atExit(path string) {
	exitMu.Lock()
	defer exitMu.Unlock()
	exitPaths = append(exitPaths, path)
}

// Main は clitest の TestMain の共通。`func TestMain(m *testing.M) { base.Main(m) }` と書く。
//
// プロセスの環境を 1 度だけ整えてからテストを回し、使い捨ての置き場を消して終わる。
//   - 走った機械・走らせた人の環境が子の答えを変えないよう、外す（GIT_*・CLAUDE_*・CCNAVI_*・GITHUB_TOKEN・GITLAB_TOKEN）。
//     CCNAVI_TEST_* は先に覚える（TestVar）。トークンがほしいテストは run.Opts.Env で渡す
//   - 走った機械の git の設定を締め出す（GitEnv）
//   - PATH から ccnavi を外す
//   - MSYS の引数の書き換えを止める
//   - 一時置き場（TMPDIR）を実パスにする（macOS）
//   - 並行の数の既定を CPU の数の 2 倍に上げる（待ちが主のテスト。-parallel を渡せばそちらが優先）
//   - go test がこのパッケージの結果を保存しないようにする（Uncache。パッケージの doc を参照）
//
// 呼び忘れは、Root・TempDir・GitEnv・run の起動が最初の 1 本で panic して知らせる。
func Main(m *testing.M) {
	// setup は TempBase を使うので、先に立てる。setup が失敗すれば下で止まり、テストは走らない。
	mainDone.Store(true)
	if err := setup(); err != nil {
		fmt.Fprintln(os.Stderr, "テストの環境を整えられない:", err)
		os.Exit(1)
	}
	// -parallel は m.Run の中の flag.Parse が後から上書きするので、渡されていればそちらが勝つ。
	_ = flag.Set("test.parallel", waitingParallel())
	code := m.Run()
	exitMu.Lock()
	for _, path := range exitPaths {
		_ = os.RemoveAll(path)
	}
	exitMu.Unlock()
	os.Exit(code)
}

var (
	mainDone   atomic.Bool
	testVars   = map[string]string{}
	exitMu     sync.Mutex
	exitPaths  []string
	gitEnv     map[string]string
	gitVarHead = "GIT_"
	// fakeVarHead は模擬スタブの設定（FAKE_*）の頭。親の値は混ぜず、必要な値は run.Opts.Env で渡す。
	fakeVarHead = "FAKE_"
)

// requireMain は Main を呼び忘れたテストを、最初の 1 本で落とす。呼ばないと git の締め出しも環境の掃除も黙って効かない。
func requireMain() {
	if !mainDone.Load() {
		panic("TestMain で base.Main(m) を呼ぶ（clitest/doc.go）")
	}
}

// TestVar は Main が外す前の CCNAVI_TEST_*（組まずに使う実行ファイルのパス）の値。
func TestVar(name string) string { return testVars[name] }

// GitEnv は走った機械の git の設定を、テストが起こす git から締め出す環境変数。Main が整えたあとの値。
//
// git は `~/.gitconfig` と `/etc/gitconfig` を読む。そこに何が入っているかは機械ごとに違い、締め出さないと
// テストの答えが「誰の機械で走らせたか」で変わる（`init.defaultBranch = main` の機械では `master` を前提にした
// テストが落ち、`commit.gpgsign = true` の機械では commit が 10 倍遅くなる）。
//
// 空の設定ではなく `init.defaultBranch` を書いた設定を指すのは、git 自身の既定に頼らないため。値はテストが前提にする
// `master` に固定する。グローバルの無視ファイル（`$XDG_CONFIG_HOME/git/ignore`）は GIT_CONFIG_GLOBAL では塞がらず、
// `core.excludesFile` を書かないかぎり git が読むので、空のファイルを指して塞ぐ（Claude Code が
// `**/.claude/settings.local.json` を書く機械で `git add -A` が設定ファイルを拾わなくなる）。
// `/dev/null` を指さないのは Windows に無いため。
func GitEnv() map[string]string {
	requireMain()
	out := make(map[string]string, len(gitEnv))
	for k, v := range gitEnv {
		out[k] = v
	}
	return out
}

// scrubEnvironment はプロセスの環境から、子の答えを変える変数を外す。
// git の変数（GIT_DIR・GIT_WORK_TREE・GIT_INDEX_FILE など、git の hook の中で回すと入る）は、子の git を別の木に向ける。
// CLAUDE_* は Claude Code の変数。CLAUDE_PROJECT_DIR は実行ファイルのルートを最優先で決め、Claude Code の中で回すと本物のリポジトリを根にする。
// FAKE_* は模擬スタブの設定。親の値は混ぜない。
// CCNAVI_* は走らせた人の設定。CCNAVI_TEST_* だけは先に覚える。GITHUB_TOKEN・GITLAB_TOKEN は走らせた人のトークン。
// 子の環境（run.Opts.Env）はここで整った環境への差分だけを足す。
func scrubEnvironment() {
	for _, kv := range os.Environ() {
		name, value, _ := strings.Cut(kv, "=")
		folded := fold(name)
		if strings.HasPrefix(folded, env.Prefix+"TEST_") {
			testVars[folded] = value
			_ = os.Unsetenv(name)
		} else if scrubbed(folded) {
			_ = os.Unsetenv(name)
		}
	}
}

// scrubbed は fold した名前の変数を子に渡さないかどうか。
func scrubbed(folded string) bool {
	return strings.HasPrefix(folded, env.Prefix) || strings.HasPrefix(folded, env.ClaudePrefix) || strings.HasPrefix(folded, gitVarHead) ||
		strings.HasPrefix(folded, fakeVarHead) || folded == fold(env.GitHubToken) || folded == fold(env.GitLabToken)
}

// fold は環境変数の名前の比べ方。Windows は大文字小文字を区別しない。
func fold(name string) string {
	if ospath.Windows {
		return strings.ToUpper(name)
	}
	return name
}

func setup() error {
	scrubEnvironment()
	if !ospath.Windows {
		if err := os.Setenv(env.Tmpdir, TempBase()); err != nil {
			return err
		}
	}
	if err := writeUncacheMark(); err != nil {
		return err
	}
	home, err := os.MkdirTemp(TempBase(), "ccnavi-gitconfig-")
	if err != nil {
		return err
	}
	atExit(home)
	config := filepath.Join(home, "config")
	excludes := filepath.Join(home, "excludes")
	if err := os.WriteFile(excludes, nil, 0o644); err != nil {
		return err
	}
	text := "[init]\n\tdefaultBranch = master\n[core]\n\texcludesFile = " + filepath.ToSlash(excludes) + "\n"
	if err := os.WriteFile(config, []byte(text), 0o644); err != nil {
		return err
	}
	gitEnv = map[string]string{
		env.GitConfigGlobal: config,
		env.GitConfigSystem: config,
		// 2.32 より前の git は GIT_CONFIG_SYSTEM を知らない。こちらは昔からある。
		env.GitConfigNoSystem: "1",
	}
	for k, v := range gitEnv {
		if err := os.Setenv(k, v); err != nil {
			return err
		}
	}
	// Git for Windows の sh（MSYS）は、ネイティブの exe に渡す引数のうち `/` を含み `:` で区切られたものを
	// パスの一覧とみなして書き換える（`refs/remotes/origin/main:.claude/x` が壊れて git が object を解けない）。
	// 止めるのは `<ref>:<path>` の形になる ref の頭だけ。全部（`*`）を止めると、sh が `/tmp/...` で渡すパスを git が読めなくなる。
	if _, ok := os.LookupEnv(env.Msys2ArgConvExcl); !ok {
		if err := os.Setenv(env.Msys2ArgConvExcl, "refs/;origin/"); err != nil {
			return err
		}
	}
	path, err := dropCcnaviFromPath(os.Getenv(env.Path))
	if err != nil {
		return err
	}
	return os.Setenv(env.Path, path)
}

// dropCcnaviFromPath は走らせた人の PATH にある ccnavi を、テストから見えなくする。
//
// 振り分けの sh と自己防衛は、bin/ に実体が無いと PATH の ccnavi を使う。PATH に ccnavi を通している機械では、
// 「実体が無い」ことを見るテストの答えが、どの機械で走らせるかで変わる。PATH を使うテストは、自分で置き場を作って PATH に足す。
// ccnavi があるディレクトリは、ccnavi 以外の中身（git など）を残すため、それらへのリンクだけを並べた使い捨ての
// ディレクトリに置き換える。
func dropCcnaviFromPath(path string) (string, error) {
	var kept []string
	for _, place := range filepath.SplitList(path) {
		entries, err := os.ReadDir(place)
		if place == "" || err != nil || !holdsCcnavi(entries) {
			kept = append(kept, place)
			continue
		}
		shim, err := os.MkdirTemp(TempBase(), "ccnavi-path-shim-")
		if err != nil {
			return "", err
		}
		atExit(shim)
		for _, entry := range entries {
			if isCcnaviName(entry.Name()) {
				continue
			}
			// リンクを作れない環境（Windows）では、その名前は見えなくなる。PATH の探し方は sh が決めるので許す。
			_ = os.Symlink(filepath.Join(place, entry.Name()), filepath.Join(shim, entry.Name()))
		}
		kept = append(kept, shim)
	}
	return strings.Join(kept, string(os.PathListSeparator)), nil
}

func isCcnaviName(name string) bool {
	folded := pathcase.Fold(name)
	return folded == "ccnavi" || folded == "ccnavi.exe"
}

func holdsCcnavi(entries []os.DirEntry) bool {
	for _, entry := range entries {
		if !entry.IsDir() && isCcnaviName(entry.Name()) {
			return true
		}
	}
	return false
}
