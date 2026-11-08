//go:build linux || darwin

package argparse

import (
	"syscall"
	"unsafe"
)

// stdoutColumns は標準出力の端末の幅。端末でなければ false。
func stdoutColumns() (int, bool) {
	var ws struct{ row, col, xpixel, ypixel uint16 }
	_, _, errno := syscall.Syscall(syscall.SYS_IOCTL, uintptr(syscall.Stdout), uintptr(syscall.TIOCGWINSZ), uintptr(unsafe.Pointer(&ws)))
	if errno != 0 {
		return 0, false
	}
	return int(ws.col), true
}
