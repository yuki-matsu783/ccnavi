//go:build windows

package fsio

import (
	"fmt"
	"syscall"
)

// osErrnoText は Python の `[WinError N] <FormatMessage の文>`。
func osErrnoText(e syscall.Errno) string {
	return fmt.Sprintf("[WinError %d] %s", int(e), e.Error())
}

// winErrnos は Win32 のエラーから Python の errno への読み替え（CPython の PC/errmap.h の主なもの）。
// 表に無いものは EINVAL になる。ERROR_DELETE_PENDING（303）がこれにあたり、待てば通る失敗として打ち直す。
var winErrnos = map[syscall.Errno]int{
	2: errnoENOENT, 3: errnoENOENT, 15: errnoENOENT, 18: errnoENOENT, 53: errnoENOENT, 67: errnoENOENT,
	161: errnoENOENT, 206: errnoENOENT,
	4: 24, 6: 9, 8: 12, 89: 11, 112: 28, 145: 41, 164: 11, 215: 11, 1816: 12,
	5: errnoEACCES, 19: errnoEACCES, 32: errnoEACCES, 33: errnoEACCES, 65: errnoEACCES, 82: errnoEACCES,
	108: errnoEACCES, 158: errnoEACCES, 167: errnoEACCES, 1920: errnoEACCES,
	17: errnoEXDEV, 80: errnoEEXIST, 183: errnoEEXIST, 267: errnoENOTDIR,
}

func osErrno(e syscall.Errno) int {
	if n, ok := winErrnos[e]; ok {
		return n
	}
	return errnoEINVAL
}
