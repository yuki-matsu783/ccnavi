package small

import (
	"encoding/json"
	"errors"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/infra/shell"
)

// このファイルは、このパッケージの sh の試験だけが使う道具。`sh -c`・bash・node は run.Command で起こす。

// pathSep は PATH の項目の区切り（Windows は `;`、ほかは `:`。Python の os.pathsep と同じ）。
var pathSep = string(filepath.ListSeparator)

// shellNames は、この機械にある sh と bash（sh が先）。
func shellNames() []string {
	var out []string
	for _, name := range []string{"sh", "bash"} {
		if _, err := exec.LookPath(name); err == nil {
			out = append(out, name)
		}
	}
	return out
}

// firstShell は sh（無ければ bash）。無ければ空。
func firstShell(t *testing.T) string {
	t.Helper()
	names := shellNames()
	if len(names) == 0 {
		t.Skip("sh が無い")
	}
	return names[0]
}

// runProgram は program を 1 回起こす（run.Command。引数を並べて渡せる形）。
func runProgram(t *testing.T, program string, o run.Opts, args ...string) run.Result {
	t.Helper()
	return run.Command(t, program, args, o)
}

// commonSh は、できあがりの姿の ccnavi-common.sh の場所。
func commonSh() string { return filepath.Join(base.ScriptsDir(), "ccnavi-common.sh") }

func writeFile(t *testing.T, path, text string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
}

// writeExe は、中身が何もしない実行可能なスタブを置く。
func writeExe(t *testing.T, path string) {
	t.Helper()
	writeFile(t, path, "#!/bin/sh\nexit 0\n")
	if err := os.Chmod(path, 0o755); err != nil {
		t.Fatal(err)
	}
}

// loadFixture は tests/fixtures の表を読む。sh と実行ファイルが同じ表で答えを見る。
func loadFixture(t *testing.T, name string, v any) {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(base.Root(), "tests", "fixtures", name))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(data, v); err != nil {
		t.Fatalf("%s: %v", name, err)
	}
}

// newWorkspace は使い捨ての木を作り、共通部（ccnavi-common*.sh）を `.ccnavi/scripts/` にコピーして返す。
func newWorkspace(t *testing.T) string {
	t.Helper()
	ws := base.TempDir(t)
	src := base.ScriptsDir()
	for _, name := range base.CommonSh(t, src) {
		data, err := os.ReadFile(filepath.Join(src, name))
		if err != nil {
			t.Fatal(err)
		}
		writeFile(t, filepath.Join(ws, ".ccnavi", "scripts", name), string(data))
	}
	return ws
}

// probe は sh の関数 fn を、値を環境変数（引数は Windows で `'` や改行を運べないため）で渡して起こし、終了コードが 0 かを返す。
func probe(t *testing.T, shell, dir, fn, value, locale string) bool {
	t.Helper()
	res := runProgram(t, shell, run.Opts{
		Dir: dir,
		Env: map[string]string{"CCNAVI_PROBE": value, "LC_ALL": locale},
	}, "-c", `. "$0"; `+fn+` "$CCNAVI_PROBE"`, commonSh())
	return res.Code == 0
}

// splitLines は `\n` で切る。末尾の改行 1 つは区切りにしない。空は nil。
func splitLines(s string) []string {
	s = strings.TrimSuffix(s, "\n")
	if s == "" {
		return nil
	}
	return strings.Split(s, "\n")
}

// readOptional は、無いファイルを空として読む。
func readOptional(t *testing.T, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return ""
	}
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

// shQuote は sh の 1 語（単引用符）。
func shQuote(s string) string { return shell.SingleQuote(s) }
