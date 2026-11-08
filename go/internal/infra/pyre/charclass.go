package pyre

import (
	"sync"
	"unicode"

	"ccnavi/internal/infra/pyfmt"
)

// casefix は Python の re の _casefix の _EXTRA_CASES（小文字にした字 → 大文字が同じになるほかの小文字）。
var casefix = map[rune][]rune{
	0x0069: {0x0131}, 0x0073: {0x017f}, 0x00b5: {0x03bc}, 0x0131: {0x0069}, 0x017f: {0x0073},
	0x0345: {0x03b9, 0x1fbe}, 0x0390: {0x1fd3}, 0x03b0: {0x1fe3}, 0x03b2: {0x03d0}, 0x03b5: {0x03f5},
	0x03b8: {0x03d1}, 0x03b9: {0x0345, 0x1fbe}, 0x03ba: {0x03f0}, 0x03bc: {0x00b5}, 0x03c0: {0x03d6},
	0x03c1: {0x03f1}, 0x03c2: {0x03c3}, 0x03c3: {0x03c2}, 0x03c6: {0x03d5}, 0x03d0: {0x03b2},
	0x03d1: {0x03b8}, 0x03d5: {0x03c6}, 0x03d6: {0x03c0}, 0x03f0: {0x03ba}, 0x03f1: {0x03c1},
	0x03f5: {0x03b5}, 0x0432: {0x1c80}, 0x0434: {0x1c81}, 0x043e: {0x1c82}, 0x0441: {0x1c83},
	0x0442: {0x1c84, 0x1c85}, 0x044a: {0x1c86}, 0x0463: {0x1c87}, 0x1c80: {0x0432}, 0x1c81: {0x0434},
	0x1c82: {0x043e}, 0x1c83: {0x0441}, 0x1c84: {0x0442, 0x1c85}, 0x1c85: {0x0442, 0x1c84},
	0x1c86: {0x044a}, 0x1c87: {0x0463}, 0x1c88: {0xa64b}, 0x1e61: {0x1e9b}, 0x1e9b: {0x1e61},
	0x1fbe: {0x0345, 0x03b9}, 0x1fd3: {0x0390}, 0x1fe3: {0x03b0}, 0xa64b: {0x1c88}, 0xfb05: {0xfb06},
	0xfb06: {0xfb05},
}

// bmpEnd は Python の文字クラスの表（charmap）が持てる範囲の終わり。ここより上は表の外で別の比べ方になる。
const bmpEnd = 0x10000

// caser は大文字小文字の無視の比べ方（Unicode か ASCII）。
type caser struct{ ascii bool }

func (c caser) lower(r rune) rune {
	if c.ascii {
		if r >= 'A' && r <= 'Z' {
			return r + 'a' - 'A'
		}
		return r
	}
	return unicode.ToLower(r)
}

func (c caser) cased(r rune) bool {
	if c.ascii {
		return r < 0x80 && (r >= 'A' && r <= 'Z' || r >= 'a' && r <= 'z')
	}
	return unicode.ToLower(r) != r || unicode.ToUpper(r) != r
}

func (c caser) fixes(r rune) []rune {
	if c.ascii {
		return nil
	}
	return casefix[r]
}

var (
	lowerOfOnce sync.Once
	lowerOf     map[rune][]rune // 小文字 → それを小文字に持つほかの字
)

// preimages は ToLower(x) == r になる x（r 自身を除く）。
func preimages(r rune) []rune {
	lowerOfOnce.Do(func() {
		lowerOf = map[rune][]rune{}
		for _, cr := range unicode.CaseRanges {
			for x := rune(cr.Lo); x <= rune(cr.Hi); x++ {
				if l := unicode.ToLower(x); l != x {
					lowerOf[l] = append(lowerOf[l], x)
				}
			}
		}
	})
	return lowerOf[r]
}

func isSpace(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f }

func categoryTest(cat category, ascii bool) func(rune) bool {
	switch cat {
	case catDigit, catNotDigit:
		f := func(r rune) bool { return r >= '0' && r <= '9' }
		if !ascii {
			f = unicode.IsDigit
		}
		if cat == catNotDigit {
			return func(r rune) bool { return !f(r) }
		}
		return f
	case catSpace, catNotSpace:
		f := func(r rune) bool { return r == ' ' || r >= '\t' && r <= '\r' }
		if !ascii {
			f = isSpace
		}
		if cat == catNotSpace {
			return func(r rune) bool { return !f(r) }
		}
		return f
	default:
		f := isASCIIWord
		if !ascii {
			f = pyfmt.IsWord
		}
		if cat == catNotWord {
			return func(r rune) bool { return !f(r) }
		}
		return f
	}
}

func isASCIIWord(r rune) bool {
	return r == '_' || r >= '0' && r <= '9' || r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z'
}

// literalTest は 1 字の照合（Python の LITERAL と NOT_LITERAL）。
func literalTest(ch rune, not bool, fl Flag) func(rune) bool {
	if fl&IgnoreCase == 0 {
		if not {
			return func(r rune) bool { return r != ch }
		}
		return func(r rune) bool { return r == ch }
	}
	c := caser{ascii: fl&unicodeOn == 0}
	if !c.cased(ch) {
		return literalTest(ch, not, 0)
	}
	lo := c.lower(ch)
	same := append([]rune{lo}, c.fixes(lo)...)
	return func(r rune) bool {
		l := c.lower(r)
		for _, s := range same {
			if l == s {
				return !not
			}
		}
		return not
	}
}

// setTest は文字クラス（Python の IN）。大文字小文字を無視するときは、Python の _optimize_charset と同じく
// 要素を小文字にした集まりに、照らす字を小文字にして比べる。要素に大文字小文字の別が無ければ比べ直さない。
func setTest(items []setItem, negate bool, fl Flag) func(rune) bool {
	ascii := fl&unicodeOn == 0
	var parts []func(rune) bool
	ignore := fl&IgnoreCase != 0
	c := caser{ascii: ascii}
	hascased := false
	for _, it := range items {
		switch it.kind {
		case setCategory:
			parts = append(parts, categoryTest(it.cat, ascii))
		case setLiteral:
			if !ignore {
				v := it.lo
				parts = append(parts, func(r rune) bool { return r == v })
				continue
			}
			lo := c.lower(it.lo)
			if c.cased(lo) {
				hascased = true
			}
			same := append([]rune{lo}, c.fixes(lo)...)
			parts = append(parts, func(r rune) bool {
				for _, s := range same {
					if r == s {
						return true
					}
				}
				return false
			})
		case setRange:
			lo, hi := it.lo, it.hi
			if !ignore {
				parts = append(parts, func(r rune) bool { return r >= lo && r <= hi })
				continue
			}
			if hi >= bmpEnd {
				// 表に収まらない範囲。Python は表の外の比べ方（RANGE_UNI_IGNORE）で範囲全体を足し、
				// 表に入った字（小文字にしたもの）もそのまま残す。
				hascased = true
				bmpHi := min(hi, bmpEnd-1)
				if lo < bmpEnd {
					parts = append(parts, loweredRange(c, lo, bmpHi))
				}
				if ascii {
					parts = append(parts, func(r rune) bool { return r >= lo && r <= hi })
				} else {
					parts = append(parts, func(r rune) bool {
						if r >= lo && r <= hi {
							return true
						}
						u := unicode.ToUpper(r)
						return u >= lo && u <= hi
					})
				}
				continue
			}
			if !hascased && anyCased(c, lo, hi) {
				hascased = true
			}
			parts = append(parts, loweredRange(c, lo, hi))
		}
	}
	lowerFirst := ignore && hascased
	return func(r rune) bool {
		if lowerFirst {
			r = c.lower(r)
		}
		for _, p := range parts {
			if p(r) {
				return !negate
			}
		}
		return negate
	}
}

func anyCased(c caser, lo, hi rune) bool {
	for x := lo; x <= hi; x++ {
		if c.cased(x) {
			return true
		}
	}
	return false
}

// loweredRange は「範囲の字を小文字にした集まり（と _casefix の仲間）」に r が入るか。r は小文字にしてから渡る。
func loweredRange(c caser, lo, hi rune) func(rune) bool {
	in := func(r rune) bool {
		if r >= lo && r <= hi && c.lower(r) == r {
			return true
		}
		if c.ascii {
			return r >= 'a' && r <= 'z' && r-('a'-'A') >= lo && r-('a'-'A') <= hi
		}
		for _, x := range preimages(r) {
			if x >= lo && x <= hi {
				return true
			}
		}
		return false
	}
	return func(r rune) bool {
		if in(r) {
			return true
		}
		for _, f := range c.fixes(r) {
			if in(f) {
				return true
			}
		}
		return false
	}
}
