package ospath

import "syscall"

// Identity は path そのもの（リンクは辿らない）の inode と変更時刻（ctime、ナノ秒）。
// lstat の st_ino と st_ctime_ns。読めなければ false。
func Identity(path string) (ino uint64, ctimeNs int64, ok bool) {
	var st syscall.Stat_t
	if err := syscall.Lstat(path, &st); err != nil {
		return 0, 0, false
	}
	return st.Ino, st.Ctim.Nano(), true
}
