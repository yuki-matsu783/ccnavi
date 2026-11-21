package core

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

// 組み立ての sh（scripts/ccnavi-build.sh）。sh は Go で `dist/ccnavi/` に組み、それを hook が起動する置き場 `.ccnavi/bin/<os>-<arch>/`
// へコピーする。振り分けの sh（`.ccnavi/scripts/ccnavi-launcher.sh`）が起動するのはこちらなので、コピーし損ねると hook は前の実行ファイルを起動し続ける。
// Go は動かさない。PATH の先頭に偽の `go` を置き、`go build` に渡った環境と引数を、組んだふりの実行ファイルの中身として書かせる。

// fakeGo は `go env GOHOSTOS` / `GOHOSTARCH` は linux/amd64 と答え、`go build -o <出力>` は出力に GOOS・GOARCH・CGO_ENABLED と引数を書く。
// FAIL_BUILD があれば 3 で失敗する。
const fakeGo = `#!/bin/sh
case "$1" in
env)
	case "$2" in GOHOSTOS) echo linux ;; GOHOSTARCH) echo amd64 ;; esac
	exit 0
	;;
build)
	[ -z "${FAIL_BUILD:-}" ] || exit 3
	all="$*"
	while [ "$#" -gt 0 ]; do
		if [ "$1" = -o ]; then out=$2; fi
		shift
	done
	printf 'GOOS=%s GOARCH=%s CGO_ENABLED=%s\n%s\n' "$GOOS" "$GOARCH" "$CGO_ENABLED" "$all" >"$out"
	chmod +x "$out"
	exit 0
	;;
esac
exit 1
`

type buildEnv struct {
	t               *testing.T
	root, bin, dist string
	tools           string
}

func newBuildEnv(t *testing.T) *buildEnv {
	if _, err := exec.LookPath("sh"); err != nil {
		t.Skip("sh が要る")
	}
	dir := emptyRoot(t)
	root := filepath.Join(dir, "repo")
	script, err := os.ReadFile(filepath.Join(base.Root(), "scripts", "ccnavi-build.sh"))
	if err != nil {
		t.Fatal(err)
	}
	writeFile(t, root, "scripts/ccnavi-build.sh", string(script))
	writeFile(t, root, "go/cmd/ccnavi/main.go", "package main\n")
	writeFile(t, root, ".gitignore", "/dist/\n/build/\n/.ccnavi/bin/\n")
	tools := filepath.Join(dir, "tools")
	if err := os.MkdirAll(tools, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(tools, "go"), []byte(fakeGo), 0o755); err != nil {
		t.Fatal(err)
	}
	return &buildEnv{t, root, filepath.Join(root, ".ccnavi", "bin"), filepath.Join(root, "dist"), tools}
}

func (b *buildEnv) build(env map[string]string, args ...string) run.Result {
	b.t.Helper()
	merged := map[string]string{"PATH": b.tools + string(os.PathListSeparator) + os.Getenv("PATH")}
	for k, v := range env {
		merged[k] = v
	}
	return run.Sh(b.t, filepath.Join(b.root, "scripts", "ccnavi-build.sh"), args, run.Opts{Env: merged})
}

func (b *buildEnv) built() string { return readFile(b.t, filepath.Join(b.dist, "ccnavi", "ccnavi")) }

func (b *buildEnv) must(r run.Result) {
	b.t.Helper()
	if r.Code != 0 {
		b.t.Fatalf("code = %d: %s%s", r.Code, r.Stdout, r.Stderr)
	}
}

func list(t *testing.T, dir string) []string {
	t.Helper()
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	var names []string
	for _, e := range entries {
		names = append(names, e.Name())
	}
	slices.Sort(names)
	return names
}

func TestBuildForThisMachineCopiesIntoItsPlace(t *testing.T) {
	t.Parallel()
	b := newBuildEnv(t)
	b.must(b.build(nil))
	built := b.built()
	mustContain(t, "組んだ物", built, "GOOS=linux GOARCH=amd64 CGO_ENABLED=0")
	if got := readFile(t, filepath.Join(b.dist, "ccnavi.target")); got != "linux-x86_64\n" {
		t.Errorf("ccnavi.target = %q", got)
	}
	live := filepath.Join(b.bin, "linux-x86_64", "ccnavi")
	if readFile(t, live) != built {
		t.Error("置き場の実行ファイルが組んだ物と違う")
	}
	// 存在は Windows でも見る。実行ビットは Windows の Go が立てないので、Windows では見ない。
	info, err := os.Stat(live)
	if err != nil {
		t.Errorf("実行できない: %v", err)
	} else if runtime.GOOS != "windows" && info.Mode()&0o111 == 0 {
		t.Errorf("実行できない: 実行ビットが無い")
	}
	// コピーであって移すのではない。dist/ は導入スクリプトの配布元。
	if !exists(filepath.Join(b.dist, "ccnavi", "ccnavi")) {
		t.Error("dist/ から消えた")
	}
}

// 組んだ実行ファイルに元のコミットを刻む。git が無い・リポジトリでないときは unknown、未コミットの変更があれば -dirty。
func TestBuildStampsTheSourceCommit(t *testing.T) {
	t.Parallel()
	b := newBuildEnv(t)
	b.must(b.build(nil))
	const stamp = "-X ccnavi/internal/entry/version.commit="
	mustContain(t, "リポジトリでない", b.built(), stamp+"unknown")

	git(t, b.root, "init", "-q")
	git(t, b.root, "add", "-A")
	git(t, b.root, "commit", "-q", "-m", "init")
	head := strings.TrimSpace(git(t, b.root, "rev-parse", "HEAD"))
	b.build(nil)
	mustContain(t, "コミット済み", b.built(), stamp+head+" ")

	writeFile(t, b.root, "go/cmd/ccnavi/main.go", "package main // x\n")
	b.build(nil)
	mustContain(t, "未コミットの変更", b.built(), stamp+head+"-dirty ")
}

func TestBuildForAnotherMachine(t *testing.T) {
	t.Parallel()
	b := newBuildEnv(t)
	b.must(b.build(nil, "--target", "windows-x86_64"))
	mustContain(t, "組んだ物", readFile(t, filepath.Join(b.dist, "ccnavi", "ccnavi.exe")), "GOOS=windows GOARCH=amd64")
	if got := readFile(t, filepath.Join(b.dist, "ccnavi.target")); got != "windows-x86_64\n" {
		t.Errorf("ccnavi.target = %q", got)
	}
	if !exists(filepath.Join(b.bin, "windows-x86_64", "ccnavi.exe")) {
		t.Error("置き場に無い")
	}
}

// dist/ccnavi/ も置き場も入れ替える。別の機械向けの組み立ての名前を残さない。
func TestBuildDoesNotKeepWhatOnlyThePreviousBuildHad(t *testing.T) {
	t.Parallel()
	b := newBuildEnv(t)
	b.build(nil, "--target", "darwin-arm64")
	writeFile(t, b.bin, "linux-x86_64/前の版の残り", "old\n")
	writeFile(t, b.bin, "darwin-x86_64/ccnavi", "other\n")
	b.must(b.build(nil))
	if got := list(t, filepath.Join(b.dist, "ccnavi")); !slices.Equal(got, []string{"ccnavi"}) {
		t.Errorf("dist/ccnavi = %v", got)
	}
	if got := list(t, filepath.Join(b.bin, "linux-x86_64")); !slices.Equal(got, []string{"ccnavi"}) {
		t.Errorf("置き場 = %v", got)
	}
	// 他の機械向けの置き場には触らない。入れ替えの途中の置き場も残さない。
	if readFile(t, filepath.Join(b.bin, "darwin-x86_64", "ccnavi")) != "other\n" {
		t.Error("他の機械向けの置き場に触った")
	}
	if got := list(t, b.bin); !slices.Equal(got, []string{"darwin-arm64", "darwin-x86_64", "linux-x86_64"}) {
		t.Errorf(".ccnavi/bin = %v", got)
	}
	if got := list(t, b.dist); !slices.Equal(got, []string{"ccnavi", "ccnavi.target"}) {
		t.Errorf("dist = %v", got)
	}
}

func TestBuildAFailedCopySaysDistIsNewAndThePlaceIsAsBefore(t *testing.T) {
	t.Parallel()
	b := newBuildEnv(t)
	writeFile(t, filepath.Dir(b.bin), "bin", "置き場を作れなくする\n")
	done := b.build(nil)
	if done.Code != 1 {
		t.Errorf("code = %d: %s%s", done.Code, done.Stdout, done.Stderr)
	}
	mustContain(t, "stderr", done.Stderr, "dist/ は新しい。.ccnavi/bin/linux-x86_64/ は前のまま")
	if !exists(filepath.Join(b.dist, "ccnavi", "ccnavi")) || readFile(t, b.bin) != "置き場を作れなくする\n" {
		t.Error("dist/ か置き場が想定と違う")
	}
}

// dist/ccnavi.target は dist/ccnavi/ の版の目印なので、入れ替えが通るまで書き換えない。
func TestBuildAFailedSwapOfDistStopsBeforeTheTargetAndThePlace(t *testing.T) {
	t.Parallel()
	b := newBuildEnv(t)
	writeFile(t, filepath.Dir(b.dist), "dist", "置き換えられなくする\n")
	done := b.build(nil)
	if done.Code != 1 {
		t.Errorf("code = %d: %s%s", done.Code, done.Stdout, done.Stderr)
	}
	mustContain(t, "stderr", done.Stderr, "dist/ccnavi/ へ置き換えられなかった")
	if readFile(t, b.dist) != "置き換えられなくする\n" || exists(b.bin) {
		t.Error("dist/ か置き場が想定と違う")
	}
}

func TestBuildAFailedGoBuildChangesNothing(t *testing.T) {
	t.Parallel()
	b := newBuildEnv(t)
	b.build(nil)
	before := b.built()
	done := b.build(map[string]string{"FAIL_BUILD": "1"}, "--target", "windows-x86_64")
	if done.Code != 3 || b.built() != before || readFile(t, filepath.Join(b.dist, "ccnavi.target")) != "linux-x86_64\n" {
		t.Errorf("code = %d: %s%s", done.Code, done.Stdout, done.Stderr)
	}
}

func TestBuildRefusesATargetItCannotRead(t *testing.T) {
	t.Parallel()
	b := newBuildEnv(t)
	for _, target := range []string{"haiku-x86_64", "linux-riscv64", "linux"} {
		if done := b.build(nil, "--target", target); done.Code != 2 || exists(b.dist) {
			t.Errorf("%s: code = %d: %s%s", target, done.Code, done.Stdout, done.Stderr)
		}
	}
}
