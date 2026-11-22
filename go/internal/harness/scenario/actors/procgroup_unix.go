//go:build !windows

package actors

import (
	"os/exec"
	"syscall"
)

// groupKill は子を新しいプロセスグループに入れ、止めるときはそのグループを殺す。殺すのは script 本体だけで、
// script の子（sh）は新しいセッションに入るため届かない。sh と孫は、script が死んで擬似端末が閉じたときの SIGHUP で終わる。
func groupKill(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	cmd.Cancel = func() error { return syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL) }
}
