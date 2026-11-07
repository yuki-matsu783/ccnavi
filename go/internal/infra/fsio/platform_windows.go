//go:build windows

package fsio

import (
	"io/fs"
	"os"
	"syscall"
)

// unlinkFile は os.remove（DeleteFileW）。os.Remove はディレクトリも消すので使わない。
func unlinkFile(path string) error {
	p, err := syscall.UTF16PtrFromString(path)
	if err == nil {
		err = syscall.DeleteFile(p)
	}
	if err != nil {
		return &fs.PathError{Op: "unlink", Path: path, Err: err}
	}
	return nil
}

// pyRename は os.rename（MoveFileExW の置き換え無し）。Windows では行き先が在れば失敗する。
func pyRename(source, target string) error {
	from, err := syscall.UTF16PtrFromString(source)
	if err != nil {
		return &os.LinkError{Op: "rename", Old: source, New: target, Err: err}
	}
	to, err := syscall.UTF16PtrFromString(target)
	if err == nil {
		err = syscall.MoveFile(from, to)
	}
	if err != nil {
		return &os.LinkError{Op: "rename", Old: source, New: target, Err: err}
	}
	return nil
}

// replace は os.replace（在る行き先を置き換える）。
var replace = os.Rename

// writable は os.access(path, os.W_OK)。Windows では読み取り専用の属性だけを見る（ディレクトリは書ける）。
var writable = func(path string) bool {
	info, err := os.Stat(path)
	if err != nil {
		return false
	}
	return info.IsDir() || info.Mode().Perm()&0o200 != 0
}

// umask は Windows には無い（Python の os.umask は 0 を返す）。
func umask() int { return 0 }

// NoFollow と NonBlock は Windows に無い（Python の getattr(os, …, 0)）。リンクは呼び手が lstat で先に見る。
const (
	NoFollow = 0
	NonBlock = 0
)
