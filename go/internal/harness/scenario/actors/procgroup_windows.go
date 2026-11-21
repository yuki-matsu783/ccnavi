//go:build windows

package actors

import "os/exec"

// Windows には擬似端末（script）が無く、RunOnTerminal は起動の前に ErrNoTerminal を返す。
func groupKill(*exec.Cmd) {}
