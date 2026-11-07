package ospath

import "syscall"

// IsTerminal は fd がコンソールか。Python の os.isatty は NUL のような文字デバイスにも真を返すが、ここはコンソールの
// モードが読めるものだけにする（ユーザの判断の経路が端末を求める確かめを、文字デバイスのリダイレクトで通さない）。
func IsTerminal(fd uintptr) bool {
	var mode uint32
	return syscall.GetConsoleMode(syscall.Handle(fd), &mode) == nil
}
