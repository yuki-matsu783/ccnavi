package useronly

import (
	"errors"
	"fmt"

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
	all := x.re.FindAll(s)
	if len(all) == 0 {
		return pyre.Found{}, false
	}
	return all[0], true
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
