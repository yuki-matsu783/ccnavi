package fsio

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"strings"
	"syscall"

	"ccnavi/internal/infra/pyfmt"
)

// Python の errno の番号。Windows の Python も CRT の番号を使うので、どの機械でも同じ値になる。
const (
	errnoENOENT  = 2
	errnoEACCES  = 13
	errnoEEXIST  = 17
	errnoEXDEV   = 18
	errnoENOTDIR = 20
	errnoEINVAL  = 22
)

// errnoMessages は自分で作る OSError の文（Python の os.strerror）。
var errnoMessages = map[int]string{
	errnoENOENT:  "No such file or directory",
	errnoEACCES:  "Permission denied",
	errnoEEXIST:  "File exists",
	errnoENOTDIR: "Not a directory",
}

// posixError は Python のコードが自分で作っていた OSError（`FileNotFoundError(errno.ENOENT, ...)` など）。
// OS の返したエラーではないので、Windows でも `[Errno N]` の形で書く。
type posixError int

func (e posixError) Error() string {
	return fmt.Sprintf("[Errno %d] %s", int(e), errnoMessages[int(e)])
}

func (e posixError) Is(target error) bool {
	switch int(e) {
	case errnoENOENT:
		return target == fs.ErrNotExist
	case errnoEEXIST:
		return target == fs.ErrExist
	case errnoEACCES:
		return target == fs.ErrPermission
	}
	return false
}

func pathError(op, path string, errno int) error {
	return &fs.PathError{Op: op, Path: path, Err: posixError(errno)}
}

func notFound(path string) error { return pathError("open", path, errnoENOENT) }

// noFilenameOps は Python がファイル名を付けずに投げる操作（開いた後の読み書きと fsync）。
var noFilenameOps = map[string]bool{"write": true, "read": true, "close": true, "sync": true, "seek": true, "fcntl": true}

// ErrorText は Python の str(OSError) と同じ文。`[Errno 2] No such file or directory: 'a'` の形で、
// 移動は `'a' -> 'b'` を付ける。書けなかった理由・読めなかった理由として呼び手へ渡す文はここで作る。
func ErrorText(err error) string {
	var link *os.LinkError
	if errors.As(err, &link) {
		return errnoText(link.Err) + ": " + pyfmt.Quote(link.Old) + " -> " + pyfmt.Quote(link.New)
	}
	var path *fs.PathError
	if errors.As(err, &path) {
		if noFilenameOps[path.Op] {
			return errnoText(path.Err)
		}
		return errnoText(path.Err) + ": " + pyfmt.Quote(path.Path)
	}
	return errnoText(err)
}

func errnoText(err error) string {
	var own posixError
	if errors.As(err, &own) {
		return own.Error()
	}
	var errno syscall.Errno
	if errors.As(err, &errno) {
		return osErrnoText(errno)
	}
	return err.Error()
}

// pyErrno は Python の OSError.errno にあたる番号。番号の無いエラーは 0。
func pyErrno(err error) int {
	var own posixError
	if errors.As(err, &own) {
		return int(own)
	}
	var errno syscall.Errno
	if errors.As(err, &errno) {
		return osErrno(errno)
	}
	return 0
}

// pyError は Python の OSError と同じ文を Error() で返し、元のエラーを Unwrap で渡す。
// 呼び手は errors.Is(err, fs.ErrNotExist) で「無い」を分けられる。
type pyError struct{ err error }

func (e pyError) Error() string { return ErrorText(e.err) }
func (e pyError) Unwrap() error { return e.err }

// Wrap は err を、Error() が Python の str(OSError) と同じ文（ErrorText）で、Unwrap で元の err を返す error にする。
// nil は nil のまま。
func Wrap(err error) error {
	if err == nil {
		return nil
	}
	return pyError{err}
}

// StrError は Python の OSError.strerror（`[Errno 13] ` を除いた文）。番号の無いエラーは空文字
// （Python の None）で、呼び手が `exc.strerror or …` の代わりを選ぶ。
func StrError(err error) string {
	if pyErrno(err) == 0 {
		return ""
	}
	text := errnoText(err)
	if _, rest, ok := strings.Cut(text, "] "); ok {
		return rest
	}
	return text
}
