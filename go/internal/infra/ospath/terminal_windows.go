package ospath

import "syscall"

// IsTerminal は fd がコンソールか。NUL のような文字デバイスは端末とみなさず、コンソールの
// モードが読めるものだけにする（ユーザの判断の経路が端末を求める確かめを、文字デバイスのリダイレクトで通さない）。
func IsTerminal(fd uintptr) bool {
	var mode uint32
	return syscall.GetConsoleMode(syscall.Handle(fd), &mode) == nil
}
