package ospath

import "syscall"

// Identity は path そのもの（リンクは辿らない）のファイルの番号と作成時刻（ナノ秒）。
// リンクをたどらずに開いたハンドルから、番号と作成時刻を読む。読めなければ false。
func Identity(path string) (ino uint64, ctimeNs int64, ok bool) {
	name, err := syscall.UTF16PtrFromString(path)
	if err != nil {
		return 0, 0, false
	}
	h, err := syscall.CreateFile(name, 0, syscall.FILE_SHARE_READ|syscall.FILE_SHARE_WRITE|syscall.FILE_SHARE_DELETE,
		nil, syscall.OPEN_EXISTING, syscall.FILE_FLAG_BACKUP_SEMANTICS|syscall.FILE_FLAG_OPEN_REPARSE_POINT, 0)
	if err != nil {
		return 0, 0, false
	}
	defer syscall.CloseHandle(h)
	var d syscall.ByHandleFileInformation
	if err := syscall.GetFileInformationByHandle(h, &d); err != nil {
		return 0, 0, false
	}
	return uint64(d.FileIndexHigh)<<32 | uint64(d.FileIndexLow), d.CreationTime.Nanoseconds(), true
}
