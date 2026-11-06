package pyjson

import (
	"math/big"
)

// Bools は Number が真偽を数として読むか。Python の isinstance(v, (int, float)) は真偽も通し、
// 型を確かめて真偽だけを外す読み手もある。
type Bools bool

const (
	// BoolsAsNumbers は真偽を 1・0 として読む（float(True) と同じ）。
	BoolsAsNumbers Bools = true
	// BoolsRejected は真偽を数として読まない。
	BoolsRejected Bools = false
)

// Number は JSON の数を float64 で読む。数でなければ false。
func Number(v any, bools Bools) (float64, bool) {
	switch x := v.(type) {
	case int:
		return float64(x), true
	case *big.Int:
		f, _ := new(big.Float).SetInt(x).Float64()
		return f, true
	case float64:
		return x, true
	case bool:
		if !bools {
			return 0, false
		}
		if x {
			return 1, true
		}
		return 0, true
	}
	return 0, false
}

// PositiveInt は正の整数ならその値、ほかは 0。真偽・小数・int に収まらない整数は 0。
func PositiveInt(v any) int {
	if n, ok := v.(int); ok && n > 0 {
		return n
	}
	return 0
}
