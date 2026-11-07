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

// İ の小文字は Python では "i̇"（i と結合の点）なので、"builtİn:" は "builtin:" で始まらない。
func TestLower(t *testing.T) {
	for in, want := range map[string]string{"DRY-RUN": "dry-run", "builtİn:x": "builti̇n:x", "\u212a": "k"} {
		if got := Lower(in); got != want {
			t.Errorf("Lower(%q) = %q, want %q", in, got, want)
		}
	}
}

// 期待は Python の str.upper()。ß と合字は 2 字以上になるので、"dismißed" も DISMISSED と同じに読む。
func TestUpper(t *testing.T) {
	for in, want := range map[string]string{
		"changes_requested": "CHANGES_REQUESTED", "dismißed": "DISMISSED", "Straße ﬁx ǰ abc": "STRASSE FIX J̌ ABC",
		"ı": "I", "ſ": "S", "": "",
	} {
		if got := Upper(in); got != want {
			t.Errorf("Upper(%q) = %q, want %q", in, got, want)
		}
	}
}

// 期待は Python の format(x, "g")。
func TestFormatG(t *testing.T) {
	for in, want := range map[float64]string{
		500: "500", 0.5: "0.5", 1e20: "1e+20", 1234567: "1.23457e+06", 1e-5: "1e-05", 123456: "123456",
		1e16: "1e+16", 0.0001: "0.0001", 100: "100", math.Inf(1): "inf", math.Inf(-1): "-inf",
	} {
		if got := FormatG(in); got != want {
			t.Errorf("FormatG(%v) = %q, want %q", in, got, want)
		}
	}
	if got := FormatG(math.NaN()); got != "nan" {
		t.Errorf("FormatG(NaN) = %q", got)
	}
}
