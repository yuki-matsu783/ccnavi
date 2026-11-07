package pyfmt

import (
	"math"
	"math/big"
	"strconv"
	"strings"
	"unicode"
)

// otherDigits は Python の str.isdigit が数字と読む字のうち、10 進の数字（Nd）でないもの（Numeric_Type が
// Digit の字。上付き・下付き・丸囲みなど）。Python 3.13（Unicode 15.1）の表。
var otherDigits = &unicode.RangeTable{
	R16: []unicode.Range16{
		{Lo: 0x00b2, Hi: 0x00b3, Stride: 1},
		{Lo: 0x00b9, Hi: 0x00b9, Stride: 1},
		{Lo: 0x1369, Hi: 0x1371, Stride: 1},
		{Lo: 0x19da, Hi: 0x19da, Stride: 1},
		{Lo: 0x2070, Hi: 0x2070, Stride: 1},
		{Lo: 0x2074, Hi: 0x2079, Stride: 1},
		{Lo: 0x2080, Hi: 0x2089, Stride: 1},
		{Lo: 0x2460, Hi: 0x2468, Stride: 1},
		{Lo: 0x2474, Hi: 0x247c, Stride: 1},
		{Lo: 0x2488, Hi: 0x2490, Stride: 1},
		{Lo: 0x24ea, Hi: 0x24ea, Stride: 1},
		{Lo: 0x24f5, Hi: 0x24fd, Stride: 1},
		{Lo: 0x24ff, Hi: 0x24ff, Stride: 1},
		{Lo: 0x2776, Hi: 0x277e, Stride: 1},
		{Lo: 0x2780, Hi: 0x2788, Stride: 1},
		{Lo: 0x278a, Hi: 0x2792, Stride: 1},
	},
	R32: []unicode.Range32{
		{Lo: 0x10a40, Hi: 0x10a43, Stride: 1},
		{Lo: 0x10e60, Hi: 0x10e68, Stride: 1},
		{Lo: 0x11052, Hi: 0x1105a, Stride: 1},
		{Lo: 0x1f100, Hi: 0x1f10a, Stride: 1},
	},
}

// IsDigit は Python の s.isdigit()。空なら偽。全角やアラビア文字の数字、上付きの ² なども数字と読む
// （int() が読めない字も含む。数として読むなら ParseInt）。
func IsDigit(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if !unicode.IsDigit(r) && !unicode.Is(otherDigits, r) {
			return false
		}
	}
	return true
}

// IsWord は Python の re の Unicode の `\w` に当たる 1 文字（文字・数字・"_"）。
func IsWord(r rune) bool { return r == '_' || unicode.IsLetter(r) || unicode.IsNumber(r) }

// IntOf は Python の int(v)。JSON か YAML から読んだ値（真偽・整数・小数・文字列）を受ける。
// 小数は 0 の側へ切り捨てる。読めない値・int に収まらない値は false。
func IntOf(v any) (int, bool) {
	switch x := v.(type) {
	case bool:
		if x {
			return 1, true
		}
		return 0, true
	case int:
		return x, true
	case *big.Int:
		if x.IsInt64() && x.Int64() >= math.MinInt && x.Int64() <= math.MaxInt {
			return int(x.Int64()), true
		}
	case float64:
		t := math.Trunc(x)
		if !math.IsInf(x, 0) && !math.IsNaN(x) && t >= math.MinInt && t < math.MaxInt {
			return int(t), true
		}
	case string:
		n, err := ParseInt(x)
		return n, err == nil
	}
	return 0, false
}

// EqualsInt は Python の v == n。v は JSON か YAML から読んだ値で、真偽は 0・1、小数は値で比べる（true も 1.0 も 1 と等しい）。
// 文字列などほかの型は等しくない。
func EqualsInt(v any, n int) bool {
	switch x := v.(type) {
	case bool:
		return x && n == 1 || !x && n == 0
	case int:
		return x == n
	case *big.Int:
		return x.IsInt64() && x.Int64() == int64(n)
	case float64:
		return x == float64(n)
	}
	return false
}

// Grouped は Python の f"{n:,}"（3 桁ごとに "," を入れる）。
func Grouped(n int) string {
	digits := strconv.Itoa(n)
	sign := ""
	if rest, ok := strings.CutPrefix(digits, "-"); ok {
		sign, digits = "-", rest
	}
	var b strings.Builder
	for i, c := range digits {
		if i > 0 && (len(digits)-i)%3 == 0 {
			b.WriteByte(',')
		}
		b.WriteRune(c)
	}
	return sign + b.String()
}
