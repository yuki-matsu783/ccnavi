//go:build !windows && !js

package ospath

import "syscall"

// Executable は呼んだ人がそのファイルを実行できるか（access(2) の X_OK）。持ち主以外にだけ実行ビットが
// 立つファイルは、持ち主からは実行できない（hook が起動すると 126 で止まる）。root はどれかが立てば実行できる。
func Executable(path string) bool {
	const xOK = 1
	return syscall.Access(path, xOK) == nil
}
