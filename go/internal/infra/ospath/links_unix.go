//go:build !windows

package ospath

import (
	"os"
	"syscall"
)

// HardLinks はファイルのハードリンクの数（os.stat の st_nlink）。数えられなければ false。
func HardLinks(path string) (uint64, bool) {
	info, err := os.Stat(path)
	if err != nil {
		return 0, false
	}
	st, ok := info.Sys().(*syscall.Stat_t)
	if !ok {
		return 0, false
	}
	return uint64(st.Nlink), true
}
