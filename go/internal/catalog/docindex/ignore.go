package docindex

import (
	"bytes"
	"errors"
	"io/fs"
	"os"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
)

const (
	// gitignoreName はツリーのルートの無視の設定。
	gitignoreName = ".gitignore"
	// IgnoreLine は索引を無視していないツリーの .gitignore に足す 1 行。
	IgnoreLine = "**/" + IndexName
	// indexFileLimit より大きい ccnavi-index.jsonl と .gitignore は、ccnavi のものとみなさず読み書きしない。
	indexFileLimit = 16 * 1024 * 1024
)

// ignoreResult は addIgnore の結果。
type ignoreResult int

const (
	notAdded ignoreResult = iota
	added
	present
)

// addIgnore は、ツリーのルートの .gitignore に IgnoreLine を 1 行足す。
// 同じ行が既にあれば足さず、結果は present とする。末尾の空白は除いて比べる。git は先頭の空白を無視しないので、
// 先頭は残して比べる。この行は効いていないこともあるが、ccnavi が書いたものではないので、足したとは言わない。
// `!` で無視を外す行（許可リスト型の `/*` と `!/docs/**` など）があっても足す。足す行は末尾に置くので、前の `!` の
// 行より後ろが勝つ。効いたかは呼び手が check-ignore で確かめ直す。既存の行は変えず、末尾に改行が無いときだけ
// 改行を補う。リンク・ふつうでないもの・読めないもの・書けないものには足さない。
func addIgnore(base string) ignoreResult {
	path := ospath.Join(base, gitignoreName)
	var existing []byte
	info, err := ospath.Lstat(path)
	switch {
	case errors.Is(err, fs.ErrNotExist):
	case err != nil:
		return notAdded
	case !info.Mode().IsRegular() || info.Size() > indexFileLimit:
		return notAdded
	default:
		if existing, err = fsio.ReadDiskBytes(path); err != nil {
			return notAdded
		}
	}
	text, _ := fsio.DecodeBytes(existing, fsio.Replace)
	for _, line := range ospath.Splitlines(text) {
		if ospath.Rstrip(line) == IgnoreLine {
			return present
		}
	}
	lead := ""
	if len(existing) > 0 && !bytes.HasSuffix(existing, []byte("\n")) {
		lead = "\n"
	}
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_APPEND|fsio.NoFollow, indexPerm)
	if err != nil {
		return notAdded
	}
	_, err = f.WriteString(lead + IgnoreLine + "\n")
	if closeErr := f.Close(); err != nil || closeErr != nil {
		return notAdded
	}
	return added
}
