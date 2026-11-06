package useronly

import (
	"errors"
	"fmt"
	"unicode/utf8"

	"ccnavi/internal/infra/pyre"
)

// buildErr は固定の式を組めなかったこと（このビルドの不備で、テストが見る）。組めなかった式は rx の nil で、
// 照合は当たった側（止める側）に倒す。止めている間に通す判定（Exempt）は buildErr があれば通さない。
var buildErr error

// rx は固定の式 1 つ。
type rx struct{ re *pyre.Regexp }

func compile(pattern string, flags pyre.Flag) rx {
	re, err := pyre.Compile(pattern, flags)
	if err != nil {
		buildErr = errors.Join(buildErr, fmt.Errorf("%q: %w", pattern, err))
	}
	return rx{re}
}

func (x rx) search(s string) bool { return x.re == nil || x.re.Search(s) }

func (x rx) match(s string) bool { return x.re == nil || x.re.Match(s) }

// first は Python の search の結果（最初に当たった所）。位置は符号位置。
func (x rx) first(s string) (pyre.Found, bool) {
	if x.re == nil {
		return pyre.Found{}, true
	}
	return x.re.Find(s)
}

// firstIn は Python の search(s, pos, endpos)。式を組めなかったときは pos に当たったものとする。
func (x rx) firstIn(s string, pos, end int) (pyre.Found, bool) {
	if x.re == nil {
		return pyre.Found{Start: pos, End: pos}, true
	}
	return x.re.FindIn(s, pos, end)
}

// matchAt は Python の match(s, pos)。式を組めなかったときは pos から終わりまで当たったものとする。
func (x rx) matchAt(s string, pos int) (pyre.Found, bool) {
	if x.re == nil {
		return pyre.Found{Start: pos, End: utf8.RuneCountInString(s)}, true
	}
	return x.re.MatchIn(s, pos, utf8.RuneCountInString(s))
}

// text は first の当たった部分（式を組めなかったときは対象の全体）。
func (x rx) text(s string) (string, bool) {
	f, ok := x.first(s)
	if !ok {
		return "", false
	}
	if x.re == nil {
		return s, true
	}
	return f.Text(), true
}

// all は Python の finditer。
func (x rx) all(s string) []pyre.Found {
	if x.re == nil {
		return nil
	}
	return x.re.FindAll(s)
}

// allIn は Python の finditer(s, pos, endpos)。式を組めなかったときは pos に 1 件当たったものとする。
func (x rx) allIn(s string, pos, end int) []pyre.Found {
	if x.re == nil {
		return []pyre.Found{{Start: pos, End: pos}}
	}
	return x.re.FindAllIn(s, pos, end)
}

// removed は Python の x.sub("", s)。式を組めなかったときは s のまま。
func (x rx) removed(s string) string {
	if x.re == nil {
		return s
	}
	return x.re.Sub(s, func(pyre.Found) string { return "" })
}
