//go:build !windows

package fsio

import (
	"io/fs"
	"os"
	"syscall"
)

// unlinkFile は os.remove。os.Remove と違って空のディレクトリも消さない（unlink(2) の失敗をそのまま返す）。
func unlinkFile(path string) error {
	if err := syscall.Unlink(path); err != nil {
		return &fs.PathError{Op: "unlink", Path: path, Err: err}
	}
	return nil
}

// pyRename は os.rename。POSIX では在る行き先を置き換える。
func pyRename(source, target string) error {
	if err := syscall.Rename(source, target); err != nil {
		return &os.LinkError{Op: "rename", Old: source, New: target, Err: err}
	}
	return nil
}

// replace は os.replace。POSIX では os.rename と同じ。
var replace = pyRename

// accessWrite は access(2) の W_OK。
const accessWrite = 0x2

// writable は os.access(path, os.W_OK)。root は権限ビットに依らず書ける。
var writable = func(path string) bool {
	return syscall.Access(path, accessWrite) == nil
}

// umask は今の umask。読むだけの手段が無いので、いったん設定して戻す。
func umask() int {
	mask := syscall.Umask(0)
	syscall.Umask(mask)
	return mask
}

// os.open に足す、Python の getattr(os, "O_NOFOLLOW", 0) と getattr(os, "O_NONBLOCK", 0)。
const (
	// NoFollow はリンクを開かせない（開けずに失敗する）。
	NoFollow = syscall.O_NOFOLLOW
	// NonBlock は名前付きパイプでも書き手を待たずに開く。
	NonBlock = syscall.O_NONBLOCK
)
