package yaml

import (
	"fmt"
	"strings"

	"ccnavi/internal/infra/ospath"
)

// Load は PyYAML の yaml.safe_load(text) と同じ値を返す。文書が無ければ nil。
// 読めない文書（構文の誤り・組み立ての途中の失敗・深すぎる入れ子）は ErrUnreadable を満たす *Error。
func Load(text string) (any, error) { return load(text, false) }

// LoadRefusingAliases は Load に、別名（*名前）を読まない制限を足したもの。別名は同じ部分木を何度も
// 辿らせて小さなファイルの中身を指数で膨らませられる（billion laughs）。別名があれば ErrAlias で断る。
func LoadRefusingAliases(text string) (any, error) { return load(text, true) }

// aliasScanDepth は、2 つ目の文書の別名を探してイベントを流すときに見る入れ子の深さ。これより深ければ
// 探すのをやめ、組み立てる段に任せる。
const aliasScanDepth = 100

func load(text string, refuseAliases bool) (any, error) {
	buf := []rune(text)
	if err := checkPrintable(buf); err != nil {
		return nil, err
	}
	buf = append(buf, 0)
	if refuseAliases && hasDocumentMark(text) {
		// 組み立てるのは最初の文書だけなので、後ろの文書の別名は先にイベントを流して断る。
		if err := refuseAliasEvents(buf); err != nil {
			return nil, err
		}
	}
	c := &composer{p: newParser(buf), refuseAliases: refuseAliases}
	root, err := c.singleNode()
	if err != nil || root == nil {
		return nil, err
	}
	return construct(root)
}

// checkPrintable は YAML が許さない文字（制御文字など）を断る。
func checkPrintable(buf []rune) error {
	for i, r := range buf {
		ok := r == '\t' || r == '\n' || r == '\r' || r >= 0x20 && r <= 0x7e || r == 0x85 ||
			r >= 0xa0 && r <= 0xd7ff || r >= 0xe000 && r <= 0xfffd || r >= 0x10000 && r <= 0x10ffff
		if !ok {
			return &Error{text: fmt.Sprintf("unacceptable character #x%04x: special characters are not allowed\n"+
				"  in \"%s\", position %d", r, sourceName, i), class: ReaderError}
		}
	}
	return nil
}

// hasDocumentMark は行頭に文書の区切り（--- か ...）があるか。無ければ文書は 1 つ。
func hasDocumentMark(text string) bool {
	for _, line := range strings.Split(text, "\n") {
		for _, mark := range []string{"---", "..."} {
			rest, ok := strings.CutPrefix(line, mark)
			if ok && (rest == "" || ospath.IsSpace([]rune(rest)[0])) {
				return true
			}
		}
	}
	return false
}

func refuseAliasEvents(buf []rune) error {
	p := newParser(buf)
	depth := 0
	for {
		ev, err := p.getEvent()
		if err != nil {
			return err
		}
		if ev == nil {
			return nil
		}
		switch ev.kind {
		case eAlias:
			return aliasError()
		case eSequenceStart, eMappingStart:
			depth++
			if depth > aliasScanDepth {
				return nil
			}
		case eSequenceEnd, eMappingEnd:
			depth--
		}
	}
}
