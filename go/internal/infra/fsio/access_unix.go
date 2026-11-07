//go:build !windows && !js

package fsio

import "syscall"

// accessWrite は access(2) の W_OK。
const accessWrite = 0x2

// writable は os.access(path, os.W_OK)。root は権限ビットに依らず書ける。
var writable = func(path string) bool {
	return syscall.Access(path, accessWrite) == nil
}

// os.open に足す、Python の getattr(os, "O_NOFOLLOW", 0) と getattr(os, "O_NONBLOCK", 0)。
const (
	// NoFollow はリンクを開かせない（開けずに失敗する）。
	NoFollow = syscall.O_NOFOLLOW
	// NonBlock は名前付きパイプでも書き手を待たずに開く。
	NonBlock = syscall.O_NONBLOCK
)
