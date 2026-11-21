//go:build !windows

package actors

import (
	"bytes"
	"os/exec"
	"sync"
	"syscall"
)

// groupKill は子を新しいプロセスグループに入れ、止めるときはグループごと殺す（孫を残さない）。
func groupKill(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	cmd.Cancel = func() error { return syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL) }
}

// lockedBuffer は標準出力と標準エラーを 1 本に集める（exec が別々の goroutine で書く）。
type lockedBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *lockedBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *lockedBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}
