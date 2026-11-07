package docindex

import (
	"bytes"
	"io"
	"os"
	"regexp"

	"ccnavi/internal/infra/fsio"
)

const (
	// firstRead は最初に読む長さ。frontmatter が無いか、ここで閉じていれば続きは読まない。
	firstRead = 4 * 1024
	// moreRead は続きを読み足す 1 回の長さ。
	moreRead = 16 * 1024
)

// closing は frontmatter を閉じる行。
var closing = regexp.MustCompile(`\n(?:---|\.\.\.)[ \t]*\r?(?:\n|$)`)

var bomBytes = []byte(bom)

// readHead は差し替えられる（テストが打ち切りを作る）。
var readHead = ReadHead

// ReadHead は、ふつうのファイルの頭を、frontmatter を探す分だけ読んで返す。リンク・ふつうでないもの・読めないものは偽。
// まず firstRead だけ読む。frontmatter が無いか、そこで閉じていれば止める。要るときだけ headLimit まで読み足す。
func ReadHead(path string) ([]byte, bool) {
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() {
		return nil, false
	}
	f, _, err := fsio.OpenNoFollow(path)
	if err != nil {
		return nil, false
	}
	defer f.Close()
	data, ok := readUpTo(f, nil, firstRead)
	if !ok {
		return nil, false
	}
	if !bytes.HasPrefix(bytes.TrimPrefix(data, bomBytes), []byte(frontFence)) {
		return data, true
	}
	for len(data) < headLimit && !closing.Match(data[min(len(data), len(frontFence)):]) {
		before := len(data)
		data, ok = readUpTo(f, data, min(moreRead, headLimit-len(data)))
		if !ok {
			return nil, false
		}
		if len(data) == before {
			break
		}
	}
	return data, true
}

func readUpTo(r io.Reader, data []byte, n int) ([]byte, bool) {
	buf := make([]byte, n)
	got, err := io.ReadFull(r, buf)
	if err != nil && err != io.EOF && err != io.ErrUnexpectedEOF {
		return nil, false
	}
	return append(data, buf[:got]...), true
}
