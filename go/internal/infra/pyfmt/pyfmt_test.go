package pyfmt

import (
	"math"
	"math/big"
	"testing"
)

// 期待値は Python 3.13 の repr が出した文字列。
func TestQuote(t *testing.T) {
	cases := []struct{ in, want string }{
		{"abc", "'abc'"},
		{"it's", `"it's"`},
		{"a'\"b", `'a\'"b'`},
		{"tab\tnl\n\r", `'tab\tnl\n\r'`},
		{"\x00\x7f", `'\x00\x7f'`},
		{"é日本", "'é日本'"},
		{"\u0085\u2028\u200b\ufeff", `'\x85\u2028\u200b\ufeff'`},
		{"😀", "'😀'"},
		{`\`, `'\\'`},
	}
	for _, c := range cases {
		if got := Quote(c.in); got != c.want {
			t.Errorf("Quote(%q) = %s, want %s", c.in, got, c.want)
		}
	}
	if got := Bytes([]byte("a'\x00\xff\"")); got != `b'a\'\x00\xff"'` {
		t.Errorf("Bytes = %s", got)
	}
}

// 指数を使うのは 1e-4 未満と 1e16 以上だけ。整数の値には .0 が付く。
func TestFloat(t *testing.T) {
	cases := []struct {
		in   float64
		want string
	}{
		{0, "0.0"}, {math.Copysign(0, -1), "-0.0"}, {1, "1.0"}, {1e16, "1e+16"},
		{9999999999999998, "9999999999999998.0"}, {1e-4, "0.0001"}, {1e-5, "1e-05"}, {0.1, "0.1"},
		{1.0 / 3, "0.3333333333333333"}, {1e300, "1e+300"}, {123456789.125, "123456789.125"},
		{2.5e-7, "2.5e-07"}, {math.Inf(-1), "-inf"}, {math.NaN(), "nan"},
	}
	for _, c := range cases {
		if got := Float(c.in); got != c.want {
			t.Errorf("Float(%v) = %s, want %s", c.in, got, c.want)
		}
	}
}

func TestStrAndTruthy(t *testing.T) {
	huge, _ := new(big.Int).SetString("123456789012345678901234567890", 10)
	strs := []struct {
		in   any
		want string
	}{
		{nil, "None"}, {true, "True"}, {3, "3"}, {huge, "123456789012345678901234567890"}, {2.0, "2.0"},
		{"x", "x"}, {[]any{"a", 1, nil}, "['a', 1, None]"}, {[]string{"a"}, "['a']"},
	}
	for _, c := range strs {
		if got := Str(c.in); got != c.want {
			t.Errorf("Str(%v) = %s, want %s", c.in, got, c.want)
		}
	}
	for _, v := range []any{nil, false, 0, 0.0, "", []any{}, new(big.Int)} {
		if Truthy(v) {
			t.Errorf("Truthy(%#v) が真", v)
		}
	}
	for _, v := range []any{true, 1, -0.5, "0", []any{nil}, math.NaN()} {
		if !Truthy(v) {
			t.Errorf("Truthy(%#v) が偽", v)
		}
	}
}
