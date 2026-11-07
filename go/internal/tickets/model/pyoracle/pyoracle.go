// Package pyoracle は model のテストが Python の実装の答えと突き合わせるための部品（テストからだけ読む）。
// リポジトリの .venv の Python で src/ccnavi を走らせて答えを取り、testdata の写しと比べる。Python が無ければ
// 写しを答えにする。写しを取り直すときは go test の -update を付ける。
package pyoracle

import (
	"bytes"
	"encoding/json"
	"flag"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

var update = flag.Bool("update", false, "testdata の Python の答えを取り直す")

// RepoRoot はリポジトリのルート。見つからなければテストを飛ばす。
func RepoRoot(t *testing.T) string {
	t.Helper()
	dir, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	for {
		if _, err := os.Stat(filepath.Join(dir, ".ccnavi", "messages")); err == nil {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			t.Skip("リポジトリが見つからない")
		}
		dir = parent
	}
}

func python(root string) string {
	for _, rel := range []string{".venv/bin/python", ".venv/Scripts/python.exe"} {
		if p := filepath.Join(root, rel); exists(p) {
			return p
		}
	}
	return ""
}

func exists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}

// Answers は script（標準入力の JSON の in を読み、答えの JSON を書く Python）の答え。testdata/<name>.json の
// 写しと違えば落とす。
func Answers[T any](t *testing.T, name, script string, in any) T {
	t.Helper()
	path := filepath.Join("testdata", name+".json")
	var saved T
	data, readErr := os.ReadFile(path)
	if readErr == nil {
		if err := json.Unmarshal(data, &saved); err != nil {
			t.Fatal(err)
		}
	}
	root := RepoRoot(t)
	py := python(root)
	if py == "" {
		if readErr != nil {
			t.Skip("Python も testdata の写しも無い")
		}
		return saved
	}
	input, err := json.Marshal(in)
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(py, "-c", script)
	cmd.Env = append(os.Environ(), "PYTHONPATH="+filepath.Join(root, "src"), "PYTHONDONTWRITEBYTECODE=1")
	cmd.Stdin = bytes.NewReader(input)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err != nil {
		t.Fatalf("python: %v\n%s", err, stderr.String())
	}
	var fresh T
	if err := json.Unmarshal(out, &fresh); err != nil {
		t.Fatalf("%v\n%s", err, out)
	}
	got, _ := json.Marshal(fresh)
	if *update {
		if err := os.MkdirAll("testdata", 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, append(got, '\n'), 0o644); err != nil {
			t.Fatal(err)
		}
	} else if readErr != nil || !bytes.Equal(bytes.TrimSpace(data), got) {
		t.Errorf("%s が Python の答えと違う（go test -run %s -update で取り直す）", path, t.Name())
	}
	return fresh
}
