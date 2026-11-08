//go:build windows

package ospath

// Executable は呼んだ人がそのファイルを実行できるか。Windows には実行の権限ビットが無いので、いつも実行できるとみなす（在るかは呼び手が見る）。
func Executable(string) bool { return true }
