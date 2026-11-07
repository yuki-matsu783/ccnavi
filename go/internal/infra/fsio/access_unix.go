//go:build !windows && !js

package fsio

import "syscall"

// accessWrite は access(2) の W_OK。
const accessWrite = 0x2

// writable は os.access(path, os.W_OK)。root は権限ビットに依らず書ける。
var writable = func(path string) bool {
	return syscall.Access(path, accessWrite) == nil
}

// os.Open の flag に足す値。この OS に無いものは 0（足しても何も変わらない）。
const (
	// NoFollow はリンクを開かせない（開けずに失敗する）。
	NoFollow = syscall.O_NOFOLLOW
	// nonBlock は名前付きパイプでも書き手を待たずに開く。
	nonBlock = syscall.O_NONBLOCK
)
