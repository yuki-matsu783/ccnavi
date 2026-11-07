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

// umask は今の umask。読むだけの手段が無いので、いったん設定して戻す。
func umask() int {
	mask := syscall.Umask(0)
	syscall.Umask(mask)
	return mask
}
