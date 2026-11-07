// Package pyshim は lifecycle のワークツリーとブランチの片付けが使う、Python の標準の 2 つの写し（shlex.quote と
// OSError.strerror）。infra に同じ部品が入ったら（infra/shell の Quote・fsio の StrError）、そちらへ寄せて消す。
package pyshim

import (
	"errors"
	"strings"
	"syscall"

	"ccnavi/internal/infra/fsio"
)

// Quote は Python の shlex.quote。
func Quote(s string) string {
	if s == "" {
		return "''"
	}
	for i := 0; i < len(s); i++ {
		c := s[i]
		safe := c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || strings.IndexByte("_@%+=:,./-", c) >= 0
		if !safe {
			return "'" + strings.ReplaceAll(s, "'", `'"'"'`) + "'"
		}
	}
	return s
}

// StrError は Python の OSError.strerror（`[Errno 13] ` と名前を除いた文）。errno の無いエラーは str(exc) の文。
func StrError(err error) string {
	var errno syscall.Errno
	if errors.As(err, &errno) {
		text := fsio.ErrorText(errno)
		if _, rest, ok := strings.Cut(text, "] "); ok {
			return rest
		}
		return text
	}
	return fsio.ErrorText(err)
}
