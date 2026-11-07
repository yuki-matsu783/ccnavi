package pyfmt

import (
	"errors"
	"math"
	"strings"
	"testing"
)

// 期待値は Python 3.13 の int()・float() が返した値と ValueError の文。
func TestParseInt(t *testing.T) {
	cases := []struct {
		in   string
		want int
		err  string
	}{
		{"12", 12, ""},
		{" +12 ", 12, ""},
		{"-12", -12, ""},
		{"1_000", 1000, ""},
		{"0_0", 0, ""},
		{"１２", 12, ""},
		{"٣_٤", 34, ""},
		{"　12　", 12, ""},
		{"-9223372036854775808", math.MinInt64, ""},
		{"1__0", 0, "invalid literal for int() with base 10: '1__0'"},
		{"_1", 0, "invalid literal for int() with base 10: '_1'"},
		{"1_", 0, "invalid literal for int() with base 10: '1_'"},
		{"+_1", 0, "invalid literal for int() with base 10: '+_1'"},
		{"++1", 0, "invalid literal for int() with base 10: '++1'"},
		{"", 0, "invalid literal for int() with base 10: ''"},
		{"0x10", 0, "invalid literal for int() with base 10: '0x10'"},
		{"1.0", 0, "invalid literal for int() with base 10: '1.0'"},
		// str.strip() と違い、ASCII の 0x1c〜0x1f は空白として外さない
		{"1\x1f", 0, `invalid literal for int() with base 10: '1\x1f'`},
	}
	for _, c := range cases {
		got, err := ParseInt(c.in)
		if c.err == "" && (err != nil || got != c.want) || c.err != "" && (err == nil || err.Error() != c.err) {
			t.Errorf("ParseInt(%q) = %d, %v; want %d, %q", c.in, got, err, c.want, c.err)
		}
	}
	if _, err := ParseInt("9223372036854775808"); !errors.Is(err, ErrIntRange) {
		t.Errorf("桁あふれ = %v", err)
	}
	if _, err := ParseInt(strings.Repeat("1", MaxIntDigits+1)); err == nil || !strings.HasPrefix(err.Error(), "Exceeds the limit (4300 digits)") {
		t.Errorf("桁の上限 = %v", err)
	}
}

func TestParseBigIntBases(t *testing.T) {
	cases := []struct {
		in        string
		base      int
		want, err string
	}{
		{"0x_1f", 16, "31", ""},
		{"1f", 16, "31", ""},
		{"0b1", 16, "177", ""},
		{"0b_1", 2, "1", ""},
		{"0o17", 8, "15", ""},
		{"١f", 16, "31", ""},
		{"0x", 16, "", "invalid literal for int() with base 16: '0x'"},
		{"0x__1", 16, "", "invalid literal for int() with base 16: '0x__1'"},
		{"_17", 8, "", "invalid literal for int() with base 8: '_17'"},
	}
	for _, c := range cases {
		got, err := ParseBigInt(c.in, c.base)
		if c.err == "" && (err != nil || got.String() != c.want) || c.err != "" && (err == nil || err.Error() != c.err) {
			t.Errorf("ParseBigInt(%q, %d) = %v, %v; want %s, %q", c.in, c.base, got, err, c.want, c.err)
		}
	}
}

func TestParseFloat(t *testing.T) {
	inf, nan := math.Inf(1), math.NaN()
	cases := []struct {
		in   string
		want float64
		ok   bool
	}{
		{"1.5", 1.5, true},
		{" 1.5 ", 1.5, true},
		{"1_0.5", 10.5, true},
		{"1e1_0", 1e10, true},
		{".5", 0.5, true},
		{"5.", 5, true},
		{"１.５", 1.5, true},
		{"inf", inf, true},
		{"-inf", -inf, true},
		{"+Infinity", inf, true},
		{"nan", nan, true},
		{"-nan", nan, true},
		{"+NaN", nan, true},
		{"1e999", inf, true},
		{"-1e999", -inf, true},
		{"1e-999", 0, true},
		{"1._5", 0, false},
		{"1_e5", 0, false},
		{"_1", 0, false},
		{"1_", 0, false},
		{"0x1p3", 0, false},
		{"1p3", 0, false},
		{"", 0, false},
		{"e5", 0, false},
		{"1e", 0, false},
		{"infinity_", 0, false},
		{"in f", 0, false},
		{"١٫٥", 0, false},
		{"1.5\x1f", 0, false},
	}
	for _, c := range cases {
		got, err := ParseFloat(c.in)
		same := got == c.want || math.IsNaN(got) && math.IsNaN(c.want)
		if c.ok && (err != nil || !same) || !c.ok && err == nil {
			t.Errorf("ParseFloat(%q) = %v, %v", c.in, got, err)
		}
	}
	if _, err := ParseFloat("1._5"); err == nil || err.Error() != "could not convert string to float: '1._5'" {
		t.Errorf("文 = %v", err)
	}
}

// 期待値は Python の s[:n]。
func TestHead(t *testing.T) {
	cases := []struct {
		in   string
		n    int
		want string
	}{
		{"日本語", 2, "日本"},
		{"日本語", 3, "日本語"},
		{"日本語", 9, "日本語"},
		{"abc", 0, ""},
		{"", 1, ""},
	}
	for _, c := range cases {
		if got := Head(c.in, c.n); got != c.want {
			t.Errorf("Head(%q, %d) = %q, want %q", c.in, c.n, got, c.want)
		}
	}
}
