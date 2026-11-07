package useronly

import (
	"unicode/utf8"

	"ccnavi/internal/infra/pyre"
)

// rx は固定の式 1 つ。Python の Pattern の呼び方（search・match・sub）に合わせた口を持つ。
type rx struct{ re *pyre.Regexp }

// compile はこのビルドに書いた固定の式を組む（組めなければ起動の時点で panic）。
func compile(pattern string, flags pyre.Flag) rx { return rx{pyre.MustCompile(pattern, flags)} }

func (x rx) search(s string) bool { return x.re.Search(s) }

func (x rx) match(s string) bool { return x.re.Match(s) }

// first は Python の search の結果（最初に当たった所）。位置は符号位置。
func (x rx) first(s string) (pyre.Found, bool) { return x.re.Find(s) }

// firstIn は Python の search(s, pos, endpos)。
func (x rx) firstIn(s string, pos, end int) (pyre.Found, bool) { return x.re.FindIn(s, pos, end) }

// matchAt は Python の match(s, pos)。
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

// all は Python の finditer。
func (x rx) all(s string) []pyre.Found { return x.re.FindAll(s) }

// allIn は Python の finditer(s, pos, endpos)。
func (x rx) allIn(s string, pos, end int) []pyre.Found { return x.re.FindAllIn(s, pos, end) }

// removed は Python の x.sub("", s)。
func (x rx) removed(s string) string {
	return x.re.Sub(s, func(pyre.Found) string { return "" })
}
