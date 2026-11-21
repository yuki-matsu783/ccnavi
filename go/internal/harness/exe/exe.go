package exe

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/ospath"
)

const (
	// buildTimeout は go build 1 回の上限（初回はキャッシュが冷えていて長い）。
	buildTimeout = 10 * time.Minute
	// staleAfter は組み立て済みの物を捨てる古さ。別のプロセスがいま使っている物を消さないよう、新しいものは残す。
	staleAfter = 24 * time.Hour
)

var sourceSuffixes = []string{".go", ".txt", ".json", ".jsonl", ".mod", ".sum"}

// 組む対象。変数の名前・パッケージ・実行ファイルの名前。
type target struct{ variable, pkg, name string }

var (
	ccnavi = target{env.TestExe, "./cmd/ccnavi", "ccnavi"}
	chrome = target{env.TestChromeExe, "./cmd/ccnavi-chrome", "ccnavi-chrome"}
	stub   = target{env.TestStubExe, "./internal/harness/cmd/teststub", "teststub"}
)

type built struct {
	path string
	err  error
}

var (
	mu    sync.Mutex
	cache = map[string]*sync.Once{}
	done  = map[string]built{}
)

// Path は ccnavi の実行ファイルのパス。初めて呼ばれたときに用意する。
func Path(t testing.TB) string { t.Helper(); return ready(t, ccnavi) }

// ChromePath は Chrome 拡張の手元向けの入口（`ccnavi-chrome <空のディレクトリ>`）のパス。
func ChromePath(t testing.TB) string { t.Helper(); return ready(t, chrome) }

// TeststubPath は sh の中から起こすスタブ（curl・userform など）と claude の台本スタブを 1 本にまとめた実行ファイルのパス。
func TeststubPath(t testing.TB) string { t.Helper(); return ready(t, stub) }

// ShPath は sh に書くための ccnavi の実行ファイルのパス。`'…'` で囲んで使う。
// 区切りを `/` にそろえる。Git Bash は `C:/…` をそのまま起こせるが、`\` は sh が食う。
func ShPath(t testing.TB) string {
	t.Helper()
	return strings.ReplaceAll(Path(t), `\`, "/")
}

func ready(t testing.TB, tg target) string {
	t.Helper()
	mu.Lock()
	once, ok := cache[tg.variable]
	if !ok {
		once = new(sync.Once)
		cache[tg.variable] = once
	}
	mu.Unlock()
	once.Do(func() {
		var b built
		if path := os.Getenv(tg.variable); path != "" {
			b.path = path
		} else {
			b.path, b.err = build(tg)
		}
		mu.Lock()
		done[tg.variable] = b
		mu.Unlock()
	})
	mu.Lock()
	b := done[tg.variable]
	mu.Unlock()
	if b.err != nil {
		t.Fatal(b.err)
	}
	return b.path
}

var (
	compatOnce  sync.Once
	compatValue string
)

// Compat は実行ファイルの互換の版（`--version` の `compat:` の値）。
func Compat(t testing.TB) string {
	t.Helper()
	exe := Path(t)
	compatOnce.Do(func() {
		out, err := exec.Command(exe, "--version").Output()
		if err != nil {
			return
		}
		for _, line := range strings.Split(string(out), "\n") {
			if v, ok := strings.CutPrefix(line, "compat:"); ok {
				compatValue = strings.TrimSpace(v)
			}
		}
	})
	if compatValue == "" {
		t.Fatal("--version に compat: の行が無い")
	}
	return compatValue
}

// HostTarget はこの機械の `<os>-<arch>`。sh（uname）とは別の読み方で求める（sh の語が食い違えば落ちる）。
// 語は scripts/ccnavi-build.sh の --target と go/internal/infra/platformtag と同じ。
func HostTarget() string {
	arch := runtime.GOARCH
	switch arch {
	case "amd64":
		arch = "x86_64"
	}
	return runtime.GOOS + "-" + arch
}

// suffix は実行ファイルの拡張子。
func suffix() string {
	if ospath.Windows {
		return ".exe"
	}
	return ""
}

// fingerprint は go/ のソースの中身と Go の版・OS・CPU から作る 16 字の指紋。
// 並べ方は Python の os.walk（その階層のファイルを辞書順に、続いてサブディレクトリを辞書順に）に合わせ、組んだ物を共有する。
func fingerprint() (string, error) {
	goDir := base.GoDir()
	h := sha256.New()
	var walk func(dir string) error
	walk = func(dir string) error {
		entries, err := os.ReadDir(dir)
		if err != nil {
			return err
		}
		var dirs, files []string
		for _, e := range entries {
			if e.IsDir() {
				dirs = append(dirs, e.Name())
			} else {
				files = append(files, e.Name())
			}
		}
		sort.Strings(dirs)
		sort.Strings(files)
		for _, name := range files {
			if !isSource(name) {
				continue
			}
			path := filepath.Join(dir, name)
			rel, _ := filepath.Rel(goDir, path)
			data, err := os.ReadFile(path)
			if err != nil {
				return err
			}
			h.Write([]byte(filepath.ToSlash(rel)))
			h.Write(data)
		}
		for _, name := range dirs {
			if err := walk(filepath.Join(dir, name)); err != nil {
				return err
			}
		}
		return nil
	}
	if err := walk(goDir); err != nil {
		return "", err
	}
	cmd := exec.Command("go", "env", "GOVERSION", "GOOS", "GOARCH")
	cmd.Dir = goDir
	out, err := cmd.Output()
	if err != nil {
		return "", fmt.Errorf("go が見つからない。Go を入れるか、%s に実行ファイルを渡す: %w", env.TestExe, err)
	}
	h.Write(out)
	return hex.EncodeToString(h.Sum(nil))[:16], nil
}

func isSource(name string) bool {
	if strings.HasSuffix(name, "_test.go") {
		return false
	}
	for _, s := range sourceSuffixes {
		if strings.HasSuffix(name, s) {
			return true
		}
	}
	return false
}

func build(tg target) (string, error) {
	fp, err := fingerprint()
	if err != nil {
		return "", err
	}
	root := filepath.Join(base.TempBase(), "ccnavi-test-exe")
	place := filepath.Join(root, fp)
	out := filepath.Join(place, tg.name+suffix())
	if info, err := os.Stat(out); err == nil && !info.IsDir() {
		return out, nil
	}
	dropStale(root)
	if err := os.MkdirAll(place, 0o755); err != nil {
		return "", err
	}
	partial := filepath.Join(place, fmt.Sprintf(".%s.%d.part%s", tg.name, os.Getpid(), suffix()))
	ctx, cancel := context.WithTimeout(context.Background(), buildTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, "go", "build", "-o", partial, tg.pkg)
	cmd.Dir = base.GoDir()
	var msg bytes.Buffer
	cmd.Stdout, cmd.Stderr = &msg, &msg
	if err := cmd.Run(); err != nil {
		return "", fmt.Errorf("go build %s に失敗した: %w\n%s", tg.pkg, err, msg.String())
	}
	if err := os.Rename(partial, out); err != nil {
		// Windows は、別のプロセスが先に置いて起こしている物を差し替えられない。中身は同じ。
		if info, statErr := os.Stat(out); statErr != nil || info.IsDir() {
			return "", err
		}
		_ = os.Remove(partial)
	}
	return out, nil
}

// dropStale は、ソースを直すたびに増える組み立て済みの物のうち、1 日より前に組んだものを消す。
func dropStale(root string) {
	entries, err := os.ReadDir(root)
	if err != nil {
		return
	}
	limit := time.Now().Add(-staleAfter)
	for _, e := range entries {
		if info, err := e.Info(); err == nil && info.ModTime().Before(limit) {
			_ = os.RemoveAll(filepath.Join(root, e.Name()))
		}
	}
}
