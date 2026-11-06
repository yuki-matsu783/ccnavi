//go:build windows

package selfguard

import "syscall"

// hardLinks はファイルのハードリンクの数。数えられなければ false。
// TODO(infra): OS の分岐は infra に置く決まり。infra に同じ部品ができたらそちらへ移す。
func hardLinks(path string) (uint64, bool) {
	name, err := syscall.UTF16PtrFromString(path)
	if err != nil {
		return 0, false
	}
	h, err := syscall.CreateFile(name, 0, syscall.FILE_SHARE_READ|syscall.FILE_SHARE_WRITE|syscall.FILE_SHARE_DELETE,
		nil, syscall.OPEN_EXISTING, syscall.FILE_FLAG_BACKUP_SEMANTICS, 0)
	if err != nil {
		return 0, false
	}
	defer syscall.CloseHandle(h)
	var info syscall.ByHandleFileInformation
	if err := syscall.GetFileInformationByHandle(h, &info); err != nil {
		return 0, false
	}
	return uint64(info.NumberOfLinks), true
}
