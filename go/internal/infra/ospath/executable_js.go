//go:build js

package ospath

import "os"

// Executable は呼んだ人がそのファイルを実行できるか。js（Chrome 拡張の WASM）には access(2) が無いので、
// 実行ビットのどれかが立っていれば実行できるとみなす。
func Executable(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.Mode().Perm()&0o111 != 0
}
