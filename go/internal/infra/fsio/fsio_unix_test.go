//go:build !windows

package fsio

import (
	"io/fs"
	"os"
	"path/filepath"
	"syscall"
	"testing"
)

// 差し替えても権限は変わらない（一時ファイルの 0600 に狭まらない）。新しいファイルは素の open と同じ umask の権限。
func TestDurableKeepsModes(t *testing.T) {
	dir := t.TempDir()
	kept := filepath.Join(dir, "kept.md")
	os.WriteFile(kept, []byte("before"), 0o640)
	os.Chmod(kept, 0o640)
	WriteFile(kept, []byte("after"), Durable)
	mask := syscall.Umask(0o022)
	defer syscall.Umask(mask)
	fresh := filepath.Join(dir, "fresh.md")
	WriteFile(fresh, []byte("x"), Durable)
	for path, want := range map[string]fs.FileMode{kept: 0o640, fresh: 0o644} {
		if info, _ := os.Stat(path); info.Mode().Perm() != want {
			t.Errorf("%s: %v, want %v", path, info.Mode().Perm(), want)
		}
	}
	if os.Geteuid() != 0 {
		os.Chmod(kept, 0o444)
		if WriteFile(kept, []byte("again"), Durable) == "" || readAll(t, kept) != "after" {
			t.Error("0444 を上書きした")
		}
	}
}
