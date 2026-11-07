package stopreview

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/pyjson"
)

// obj は JSON の文からオブジェクトを作る（判定役の結果・記録の行）。
func obj(t *testing.T, text string) *pyjson.Object {
	t.Helper()
	v, err := pyjson.Loads(text)
	if err != nil {
		t.Fatalf("%s: %v", text, err)
	}
	o, ok := v.(*pyjson.Object)
	if !ok {
		t.Fatalf("%s: オブジェクトでない", text)
	}
	return o
}

func load(t *testing.T, text string) any {
	t.Helper()
	v, err := pyjson.Loads(text)
	if err != nil {
		t.Fatalf("%s: %v", text, err)
	}
	return v
}

func writeFile(t *testing.T, path, text string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
}

// lines は JSON の行を書いた transcript を作る。
func lines(t *testing.T, dir string, rows ...string) string {
	t.Helper()
	path := filepath.Join(dir, "t.jsonl")
	writeFile(t, path, strings.Join(rows, "\n")+"\n")
	return path
}

func dumps(t *testing.T, v any) string {
	t.Helper()
	s, err := pyjson.Dumps(v, pyjson.Options{RawUnicode: true})
	if err != nil {
		t.Fatal(err)
	}
	return s
}
