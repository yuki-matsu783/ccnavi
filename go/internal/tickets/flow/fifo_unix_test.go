//go:build !windows

package flow

import (
	"path/filepath"
	"syscall"
	"testing"
	"time"
)

func TestFifoIsNotReadAndDoesNotHang(t *testing.T) {
	root := t.TempDir()
	fifo := filepath.Join(root, "flow.yml")
	if err := syscall.Mkfifo(fifo, 0o644); err != nil {
		t.Skip(err)
	}
	done := make(chan [2]string, 1)
	go func() {
		_, why := Load(fifo, root)
		done <- [2]string{why, Fingerprint(fifo, root)}
	}()
	select {
	case got := <-done:
		if got != [2]string{notRegular, markUnreadable + notRegular} {
			t.Errorf("%q", got)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("FIFO を開いて固まった")
	}
}
