//go:build !windows

package start

import (
	"os/exec"
	"path/filepath"
	"syscall"
	"testing"
	"time"

	"ccnavi/internal/harness/run"
)

// dash は EXIT の trap を TERM で走らせない。TERM でもロックを外す。
func TestSyncATerminatedRunReleasesItsLockUnderDash(t *testing.T) {
	t.Parallel()
	dash, err := exec.LookPath("dash")
	if err != nil {
		t.Skip("dash が無い")
	}
	s := newSync(t)
	write(t, filepath.Join(s.scripts, "ccnavi-review.sh"), "#!/bin/sh\nsleep 30\n")
	s.deleteRemoteBranch(syncParent)
	cmd := exec.Command(dash, filepath.Join(s.scripts, "ccnavi-sync.sh"), syncParent)
	cmd.Dir = s.ws
	cmd.Env = run.Environment(run.Opts{Env: s.env(nil)})
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	group := func(sig syscall.Signal) { _ = syscall.Kill(-cmd.Process.Pid, sig) }
	t.Cleanup(func() { group(syscall.SIGKILL); _ = cmd.Wait() })
	owner := filepath.Join(s.lockDir(), "owner")
	deadline := time.Now().Add(60 * time.Second)
	for !exists(owner) && time.Now().Before(deadline) {
		time.Sleep(100 * time.Millisecond)
	}
	truth(t, isDir(s.lockDir()), "ロックを取る前に終わった")
	// ターミナルの Ctrl-C と同じく、プロセスのまとまりへ送る（子も止まり、sh の trap が走る）。
	group(syscall.SIGTERM)
	done := make(chan struct{})
	go func() { _ = cmd.Wait(); close(done) }()
	select {
	case <-done:
	case <-time.After(60 * time.Second):
		t.Fatal("TERM で終わらない")
	}
	truth(t, !exists(s.lockDir()), "TERM で終わったのにロックが残っている")
}
