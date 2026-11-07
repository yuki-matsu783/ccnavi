package handoffnames

import (
	_ "embed"
	"fmt"
	"strings"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyfmt"
)

// namesFile は表のファイル名。`.ccnavi/scripts/` に置き、sh と実行ファイルが同じものを読む。
const namesFile = "ccnavi-handoff-names.txt"

const (
	commentMark = "#"
	msgBadLine  = namesFile + " の %d 行目を読めない: %s"
)

//go:embed ccnavi-handoff-names.txt
var embedded string

// Pair は表の 1 行（元の階層名 → 下書きでの階層名）。
type Pair struct {
	Original string
	Draft    string
}

// Table は読み替えの表。行の順に当てる（先に当たった行を使う）。
type Table struct{ pairs []Pair }

var defaultTable, defaultErr = parse(embedded)

// Default は埋め込んだ表。中身はテストで確かめているので、エラーは写しを壊したときだけ。
func Default() (*Table, error) { return defaultTable, defaultErr }

// parse は表のファイルの中身を読む。`#` で始まる行と空行は読まない。1 行は空白で区切った 2 語だけで、
// 読めない行はエラーにする（黙って捨てると、読み替えられない名前のまま下書きが置かれる）。
// 同じ元の名前を 2 度書けば、後の行の名前を最初の行の位置で使う（辞書への代入と同じ）。
func parse(text string) (*Table, error) {
	t := &Table{}
	for i, raw := range ospath.Splitlines(text) {
		line := ospath.Strip(raw)
		if line == "" || strings.HasPrefix(line, commentMark) {
			continue
		}
		words := ospath.Fields(line)
		if len(words) != 2 || !usable(words[0]) || !usable(words[1]) {
			return nil, fmt.Errorf(msgBadLine, i+1, pyfmt.Quote(raw))
		}
		t.put(Pair{words[0], words[1]})
	}
	return t, nil
}

func usable(word string) bool { return isASCII(word) && !strings.Contains(word, "/") }

func (t *Table) put(p Pair) {
	for i := range t.pairs {
		if t.pairs[i].Original == p.Original {
			t.pairs[i].Draft = p.Draft
			return
		}
	}
	t.pairs = append(t.pairs, p)
}

// ToDraft は元の相対パス（"/" 区切り）を、下書きの置き場の中の相対パスにする。
func (t *Table) ToDraft(rel string) string {
	return t.convert(rel, func(p Pair) (string, string) { return p.Original, p.Draft })
}

// FromDraft は下書きの置き場の中の相対パスを、元の相対パスにする。綴りは表の元の階層名にそろう。
func (t *Table) FromDraft(rel string) string {
	return t.convert(rel, func(p Pair) (string, string) { return p.Draft, p.Original })
}

func (t *Table) convert(rel string, way func(Pair) (from, to string)) string {
	parts := strings.Split(rel, "/")
	for i, part := range parts {
		for _, p := range t.pairs {
			if from, to := way(p); same(part, from) {
				parts[i] = to
				break
			}
		}
	}
	return strings.Join(parts, "/")
}

func same(name, other string) bool {
	return isASCII(name) && pathcase.Fold(name) == pathcase.Fold(other)
}

func isASCII(s string) bool {
	for i := 0; i < len(s); i++ {
		if s[i] >= 0x80 {
			return false
		}
	}
	return true
}
