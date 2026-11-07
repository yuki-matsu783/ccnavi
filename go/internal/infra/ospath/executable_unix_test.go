//go:build !windows && !js

package ospath

import (
	"os"
	"path/filepath"
	"testing"
)

// 実行ビットが立つファイルだけが実行できる。root は持ち主の実行ビットが無くても実行できるので、その形は見ない。
func TestExecutable(t *testing.T) {
	dir := t.TempDir()
	run := filepath.Join(dir, "run.sh")
	plain := filepath.Join(dir, "plain.sh")
	for path, mode := range map[string]os.FileMode{run: 0o755, plain: 0o644} {
		if err := os.WriteFile(path, nil, mode); err != nil {
			t.Fatal(err)
		}
		if err := os.Chmod(path, mode); err != nil {
			t.Fatal(err)
		}
	}
	if !Executable(run) || Executable(plain) || Executable(filepath.Join(dir, "missing")) {
		t.Errorf("%v %v", Executable(run), Executable(plain))
	}
}
