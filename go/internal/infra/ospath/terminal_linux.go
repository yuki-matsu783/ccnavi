package ospath

import (
	"syscall"
	"unsafe"
)

// IsTerminal は fd がターミナルか（Python の os.isatty。tcgetattr が通るか）。
func IsTerminal(fd uintptr) bool {
	var t syscall.Termios
	_, _, errno := syscall.Syscall(syscall.SYS_IOCTL, fd, uintptr(syscall.TCGETS), uintptr(unsafe.Pointer(&t)))
	return errno == 0
}
