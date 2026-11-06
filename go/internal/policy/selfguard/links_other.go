//go:build !windows

package selfguard

import (
	"os"
	"syscall"
)

// hardLinks はファイルのハードリンクの数。数えられなければ false。
// TODO(infra): OS の分岐は infra に置く決まり。infra に同じ部品ができたらそちらへ移す。
func hardLinks(path string) (uint64, bool) {
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
