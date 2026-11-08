package useronly

import (
	"unicode/utf8"

	"ccnavi/internal/infra/pyre"
)

// rx は固定の式 1 つ。search・match・sub の呼び方で使えるようにした口である。
type rx struct{ re *pyre.Regexp }

// compile はこのビルドに書いた固定の式を組む（組めなければ起動の時点で panic）。
func compile(pattern string, flags pyre.Flag) rx { return rx{pyre.MustCompile(pattern, flags)} }

func (x rx) search(s string) bool { return x.re.Search(s) }

func (x rx) match(s string) bool { return x.re.Match(s) }

// first は最初に当たった所（search）。位置は符号位置。
func (x rx) first(s string) (pyre.Found, bool) { return x.re.Find(s) }

// firstIn は pos から end までの範囲での search。
func (x rx) firstIn(s string, pos, end int) (pyre.Found, bool) { return x.re.FindIn(s, pos, end) }

// matchAt は pos の位置から始まる当たり（match）。
func (x rx) matchAt(s string, pos int) (pyre.Found, bool) {
	return x.re.MatchIn(s, pos, utf8.RuneCountInString(s))
}

// text は first の当たった部分。
func (x rx) text(s string) (string, bool) {
	f, ok := x.first(s)
	if !ok {
		return "", false
	}
	return f.Text(), true
}

// all は当たった所を全部返す。
func (x rx) all(s string) []pyre.Found { return x.re.FindAll(s) }

// allIn は pos から end までの範囲で、当たった所を全部返す。
func (x rx) allIn(s string, pos, end int) []pyre.Found { return x.re.FindAllIn(s, pos, end) }

// removed は当たった所を消した文字列。
func (x rx) removed(s string) string {
	return x.re.Sub(s, func(pyre.Found) string { return "" })
}
