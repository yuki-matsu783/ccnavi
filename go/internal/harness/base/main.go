package base

import (
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/pathcase"
)

var (
	exitMu    sync.Mutex
	exitPaths []string
	gitEnv    map[string]string
)

// atExit は Main が終わりに消す置き場を覚える。
func atExit(path string) {
	exitMu.Lock()
	defer exitMu.Unlock()
	exitPaths = append(exitPaths, path)
}

// Main は clitest の TestMain の共通。`func TestMain(m *testing.M) { base.Main(m) }` と書く。
//
// プロセスの環境を 1 度だけ整えてからテストを回し、使い捨ての置き場を消して終わる。
//   - 走った機械の git の設定を締め出す（GitEnv）
//   - PATH から ccnavi を外す
//   - MSYS の引数の書き換えを止める
//
// go test のキャッシュで前の結果を返されないよう、-count=1 でない起動は断る（パッケージの doc を参照）。
func Main(m *testing.M) {
	flag.Parse()
	if f := flag.Lookup("test.testlogfile"); f != nil && f.Value.String() != "" {
		fmt.Fprintln(os.Stderr, "clitest は go test -count=1 で回す。sh・文面・見本はテストのプロセスが読まないので、go test のキャッシュは変更に気づかず前の結果を返す")
		os.Exit(1)
	}
	if err := setup(); err != nil {
		fmt.Fprintln(os.Stderr, "テストの環境を整えられない:", err)
		os.Exit(1)
	}
	code := m.Run()
	exitMu.Lock()
	for _, path := range exitPaths {
		_ = os.RemoveAll(path)
	}
	exitMu.Unlock()
	os.Exit(code)
}

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
	out := make(map[string]string, len(gitEnv))
	for k, v := range gitEnv {
		out[k] = v
	}
	return out
}

func setup() error {
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
	return dropCcnaviFromPath()
}

// dropCcnaviFromPath は走らせた人の PATH にある ccnavi を、テストから見えなくする。
//
// 振り分けの sh と自己防衛は、bin/ に実体が無いと PATH の ccnavi を使う。PATH に ccnavi を通している機械では、
// 「実体が無い」ことを見るテストの答えが、どの機械で走らせるかで変わる。PATH を使うテストは、自分で置き場を作って PATH に足す。
// ccnavi があるディレクトリは、ccnavi 以外の中身（git など）を残すため、それらへのリンクだけを並べた使い捨ての
// ディレクトリに置き換える。
func dropCcnaviFromPath() error {
	var kept []string
	for _, place := range filepath.SplitList(os.Getenv(env.Path)) {
		entries, err := os.ReadDir(place)
		if place == "" || err != nil || !holdsCcnavi(entries) {
			kept = append(kept, place)
			continue
		}
		shim, err := os.MkdirTemp(TempBase(), "ccnavi-path-shim-")
		if err != nil {
			return err
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
	return os.Setenv(env.Path, strings.Join(kept, string(os.PathListSeparator)))
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
