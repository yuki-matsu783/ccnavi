//go:build !windows

package fsio

import (
	"fmt"
	"syscall"
	"unicode"
	"unicode/utf8"
)

// osErrnoText は C の strerror と同じ文。Go の文は先頭が小文字なだけで、ほかは同じ。
func osErrnoText(e syscall.Errno) string {
	msg := e.Error()
	r, size := utf8.DecodeRuneInString(msg)
	return fmt.Sprintf("[Errno %d] %s", int(e), string(unicode.ToUpper(r))+msg[size:])
}

func osErrno(e syscall.Errno) int { return int(e) }
