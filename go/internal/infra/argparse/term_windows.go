package argparse

import (
	"syscall"
	"unsafe"
)

var getConsoleScreenBufferInfo = syscall.NewLazyDLL("kernel32.dll").NewProc("GetConsoleScreenBufferInfo")

// consoleInfo は CONSOLE_SCREEN_BUFFER_INFO。
type consoleInfo struct {
	size, cursor             [2]int16
	attributes               uint16
	left, top, right, bottom int16
	maxSize                  [2]int16
}

// stdoutColumns は Python の os.get_terminal_size(1).columns（見えている窓の幅）。コンソールでなければ false。
func stdoutColumns() (int, bool) {
	h, err := syscall.GetStdHandle(syscall.STD_OUTPUT_HANDLE)
	if err != nil {
		return 0, false
	}
	var info consoleInfo
	if ok, _, _ := getConsoleScreenBufferInfo.Call(uintptr(h), uintptr(unsafe.Pointer(&info))); ok == 0 {
		return 0, false
	}
	return int(info.right) - int(info.left) + 1, true
}
