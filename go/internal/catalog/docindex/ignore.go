package docindex

import (
	"bytes"
	"os"
	"strings"

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

// negatesIndex は ccnavi-index.jsonl を無視から外しうる `!` の行か（`!*.jsonl` のような広い行も含む）。
func negatesIndex(line string) bool {
	pattern, ok := strings.CutPrefix(line, "!")
	if !ok {
		return false
	}
	pattern = rstrip(pattern)
	return strings.HasSuffix(pattern, IndexName) || strings.HasSuffix(pattern, "*.jsonl") || strings.HasSuffix(pattern, "*")
}

// addIgnore は、ツリーのルートの .gitignore に IgnoreLine を 1 行足す。
// 同じ行が既にあれば足さず、結果は present とする。末尾の空白は除いて比べる。git は先頭の空白を無視しないので、
// 先頭は残して比べる。この行は効いていないこともあるが、ccnavi が書いたものではないので、足したとは言わない。
// `!` で索引を否定する行があれば、利用者が追跡しているとみなして足さない。既存の行は変えず、末尾に改行が無いときだけ
// 改行を補う。リンク・ふつうでないもの・読めないもの・書けないものには足さない。
func addIgnore(base string) ignoreResult {
	path := ospath.Join(base, gitignoreName)
	var existing []byte
	info, err := os.Lstat(path)
	switch {
	case os.IsNotExist(err):
	case err != nil:
		return notAdded
	case !info.Mode().IsRegular() || info.Size() > indexFileLimit:
		return notAdded
	default:
		if existing, err = os.ReadFile(path); err != nil {
			return notAdded
		}
	}
	text, _ := fsio.DecodeBytes(existing, fsio.Replace)
	lines := ospath.Splitlines(text)
	for _, line := range lines {
		if negatesIndex(ospath.Strip(line)) {
			return notAdded
		}
	}
	for _, line := range lines {
		if rstrip(line) == IgnoreLine {
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
