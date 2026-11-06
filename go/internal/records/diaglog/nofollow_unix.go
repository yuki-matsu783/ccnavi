//go:build !windows

package diaglog

import "syscall"

// noFollow はリンクの書き込み先を開かせない（開けずに失敗する）。
const noFollow = syscall.O_NOFOLLOW
