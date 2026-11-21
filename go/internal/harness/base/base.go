package base

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"ccnavi/internal/infra/ospath"
)

// 共通レイヤーと config（自身のレイヤー）の 3 本のファイルの置き場。置き場の名前はテストの側に持つ。
// 既定が動いたら、ここを置いた木で実行ファイルが設定を読まなくなり、設定を前提にしたテストが落ちて気づく。
const projectHome = ".ccnavi"

var layerFileNames = map[string]string{"rules": "rules.yml", "phases": "phases.yml", "risk": "risks.yml"}

// tempPrefix は使い捨ての置き場の名前の頭。Windows の MAX_PATH に当たらないよう短くする。
const tempPrefix = "cc-"

var (
	rootOnce sync.Once
	rootPath string
)

// Root はリポジトリの根（go/go.mod の親）。テストの作業ディレクトリから上って探す。
// 呼ぶたびに Uncache する（go test に結果を保存させない）。
func Root() string {
	requireMain()
	Uncache()
	rootOnce.Do(func() {
		dir, _, err := findRoot()
		if err != nil {
			panic(err)
		}
		rootPath = dir
	})
	return rootPath
}

// findRoot は作業ディレクトリから上って go/go.mod を持つ所を探す。作業ディレクトリからの相対パス（`../..`）も返す。
func findRoot() (dir, rel string, err error) {
	dir, err = os.Getwd()
	if err != nil {
		return "", "", err
	}
	rel = "."
	for {
		if _, err := os.Stat(filepath.Join(dir, "go", "go.mod")); err == nil {
			return dir, rel, nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return "", "", errors.New("リポジトリの根（go/go.mod の親）が見つからない")
		}
		dir = parent
		rel = filepath.Join(rel, "..")
	}
}

// GoDir は Go のモジュールの根（<リポジトリの根>/go）。
func GoDir() string { return filepath.Join(Root(), "go") }

// realpath はリンクを解いた絶対パス。在らないパスは絶対パスにして返す。
func realpath(path string) string { return ospath.Realpath(path) }

// TempBase は使い捨ての置き場の親。リンクを解いた実パスにそろえる。macOS の一時置き場は `/var/...` だが実体は
// `/private/var/...` で、git や realpath は後者を返す。文字列をそのまま比べるテストが機械の違いで食い違わないようにする。
func TempBase() string {
	requireMain()
	return realpath(os.TempDir())
}

// TempDir は使い捨ての置き場を作り、テストの終わりに消す。名前は短い（Windows の MAX_PATH）。
// t.TempDir は使わない（テスト名が長く、後片付けの失敗でテストを落とすため）。後片付けの失敗は無視する。
func TempDir(t testing.TB) string {
	t.Helper()
	dir, err := os.MkdirTemp(TempBase(), tempPrefix)
	if err != nil {
		t.Fatal(err)
	}
	dir = realpath(dir)
	t.Cleanup(func() { _ = os.RemoveAll(dir) })
	return dir
}

// CommonRelpath は共通レイヤーのファイル（rules / phases / risk）の、ワークスペースルートからの相対パス。
func CommonRelpath(kind string) string {
	return filepath.Join(projectHome, "config", "common", layerFileNames[kind])
}

// CommonPath はそのワークスペースルートの共通レイヤーのファイルのパス。
func CommonPath(root, kind string) string { return filepath.Join(root, CommonRelpath(kind)) }

// ConfigPath はワークスペースルートの config（自身のレイヤー）のファイルのパス。
// フェーズ定義は共通レイヤーと config の両方に置け、判定は 2 本の和を読む。
func ConfigPath(root, kind string) string {
	return filepath.Join(root, projectHome, "config", "project", layerFileNames[kind])
}

var (
	fixtureMu sync.Mutex
	fixtureWS = map[string]string{}
)

// FixtureWorkspace は tests/fixtures/<name> を共通レイヤーのルールに据えたワークスペースルート。
// `--rules` は診断でだけ有効なので、見本のルールを指すのには使わず、`--root` にここを渡す。
// リポジトリ自身をルートにしないので、走った機械の共通レイヤーが判定に入り込まない。
// 組むのは見本ごとにプロセスで 1 度。中身は読むだけなので使い回してよい（Main が後片付けする）。
func FixtureWorkspace(t testing.TB, name string) string {
	t.Helper()
	fixtureMu.Lock()
	defer fixtureMu.Unlock()
	if ws, ok := fixtureWS[name]; ok {
		return ws
	}
	ws, err := os.MkdirTemp(TempBase(), tempPrefix)
	if err != nil {
		t.Fatal(err)
	}
	ws = realpath(ws)
	atExit(ws)
	data, err := os.ReadFile(filepath.Join(Root(), "tests", "fixtures", name))
	if err != nil {
		t.Fatal(err)
	}
	target := CommonPath(ws, "rules")
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(target, data, 0o644); err != nil {
		t.Fatal(err)
	}
	fixtureWS[name] = ws
	return ws
}

var (
	probeMu sync.Mutex
	probes  = map[string]bool{}
)

// probe は確かめを 1 度だけやって覚える。
func probe(name string, check func(dir string) bool) bool {
	probeMu.Lock()
	defer probeMu.Unlock()
	if v, ok := probes[name]; ok {
		return v
	}
	dir, err := os.MkdirTemp(TempBase(), tempPrefix)
	if err != nil {
		return false
	}
	defer func() { _ = os.RemoveAll(dir) }()
	probes[name] = check(dir)
	return probes[name]
}

// CanSymlink はシンボリックリンクを実際に作れるか。Windows は特権が無いと作れない。
// OS の名前では決めず、使い捨ての置き場で 1 本作って確かめる。
func CanSymlink() bool {
	return probe("symlink", func(dir string) bool {
		return os.Symlink(dir, filepath.Join(dir, "link")) == nil
	})
}

// CanCaseSensitive は大文字小文字だけが違う名前を、別のファイルとして並べて作れるか。
// macOS（既定）と Windows のファイルシステムは区別しないので、`a` と `A` が同じ 1 本になる。
func CanCaseSensitive() bool {
	return probe("case", func(dir string) bool {
		for _, name := range []string{"a", "A"} {
			if os.WriteFile(filepath.Join(dir, name), []byte(name), 0o644) != nil {
				return false
			}
		}
		entries, err := os.ReadDir(dir)
		return err == nil && len(entries) == 2
	})
}

// CanPassArgument は sh から jq へ、size 字の引数を渡せるか。実際に渡して確かめる。
// 1 引数の上限（Windows は約 32,000 字）を超える場面は、上限のある環境では取れない。
// sh か jq が無ければ、確かめずに true を返す（そちらは別の skip が受ける）。
func CanPassArgument(size int) bool {
	sh := shellPath()
	if sh == "" {
		return true
	}
	if _, err := exec.LookPath("jq"); err != nil {
		return true
	}
	cmd := exec.Command(sh, "-c", `a=$(cat); jq -n --arg a "$a" '$a|length'`)
	cmd.Stdin = strings.NewReader(strings.Repeat("x", size))
	out, err := cmd.Output()
	return err == nil && strings.TrimSpace(string(out)) == fmt.Sprint(size)
}

func shellPath() string {
	for _, name := range []string{"sh", "bash"} {
		if path, err := exec.LookPath(name); err == nil {
			return path
		}
	}
	return ""
}

// LiveShPid は sh の `kill -0` から生きて見えるプロセスの pid。テストの終わりに止める。
// ロックの持ち主を「生きているプロセス」にしたいテストが使う。Go の pid は Windows の Git Bash（MSYS）の pid の表に無く、
// sh からは死んで見える。sh 自身に起こさせた sleep の pid（sh の `$$`）なら、どこでも sh から生きて見える。
func LiveShPid(t testing.TB) int {
	t.Helper()
	sh := shellPath()
	if sh == "" {
		t.Skip("sh が無い")
	}
	cmd := exec.Command(sh, "-c", "echo $$; exec sleep 600")
	out, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = cmd.Process.Kill(); _ = cmd.Wait() })
	var pid int
	if _, err := fmt.Fscanln(out, &pid); err != nil {
		t.Fatalf("sh の pid を読めない: %v", err)
	}
	return pid
}

// ExecTimeout は子のプロセス 1 回の上限。
const ExecTimeout = 120 * time.Second
