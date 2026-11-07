//go:build js

package fsio

import "os"

// writable は os.access(path, os.W_OK)。js（Chrome 拡張の WASM）には access(2) が無いので、仮想のファイルシステムの
// 権限ビットを見る。
var writable = func(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.Mode().Perm()&0o200 != 0
}

// js には O_NOFOLLOW・O_NONBLOCK が無い（Python の getattr(os, ..., 0) と同じく 0）。
const (
	NoFollow = 0
	NonBlock = 0
)
