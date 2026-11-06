package pyfmt

import (
	"math"
	"testing"
	"time"
)

// 期待値は Python 3.13 の str.isdigit・int()・f"{n:,}"・datetime.fromisoformat の答え。
func TestIsDigit(t *testing.T) {
	for s, want := range map[string]bool{"": false, "123": true, "１２": true, "²": true, "①": true, "½": false, "1.5": false, "-1": false} {
		if got := IsDigit(s); got != want {
			t.Errorf("IsDigit(%q) = %v, want %v", s, got, want)
		}
	}
}

func TestIsWord(t *testing.T) {
	for r, want := range map[rune]bool{'a': true, '_': true, '9': true, 'あ': true, '²': true, '-': false, ' ': false, '/': false} {
		if got := IsWord(r); got != want {
			t.Errorf("IsWord(%q) = %v, want %v", r, got, want)
		}
	}
}

func TestIntOf(t *testing.T) {
	cases := []struct {
		in   any
		want int
		ok   bool
	}{
		{true, 1, true}, {3, 3, true}, {2.9, 2, true}, {-2.9, -2, true}, {" 7 ", 7, true},
		{"x", 0, false}, {math.Inf(1), 0, false}, {1e300, 0, false}, {nil, 0, false},
	}
	for _, c := range cases {
		if got, ok := IntOf(c.in); got != c.want || ok != c.ok {
			t.Errorf("IntOf(%v) = %d, %v", c.in, got, ok)
		}
	}
}

func TestGrouped(t *testing.T) {
	for n, want := range map[int]string{0: "0", 999: "999", 1000: "1,000", 1234567: "1,234,567", -1234: "-1,234"} {
		if got := Grouped(n); got != want {
			t.Errorf("Grouped(%d) = %q", n, got)
		}
	}
}

func TestFromISOFormat(t *testing.T) {
	cases := []struct {
		in        string
		want      time.Time
		zoned, ok bool
	}{
		{"2026-01-02T03:04:05+09:00", time.Date(2026, 1, 1, 18, 4, 5, 0, time.UTC), true, true},
		{"20260102", time.Date(2026, 1, 2, 0, 0, 0, 0, time.UTC), false, true},
		{"2026-01-02 03:04:05.5Z", time.Date(2026, 1, 2, 3, 4, 5, 500_000_000, time.UTC), true, true},
		{"2026-02-30", time.Time{}, false, false},
		{"２０２６-01-02", time.Time{}, false, false}, // ASCII の外の数字は読まない
	}
	for _, c := range cases {
		got, zoned, ok := FromISOFormat(c.in)
		if ok != c.ok || zoned != c.zoned || !got.Equal(c.want) {
			t.Errorf("FromISOFormat(%q) = %v, %v, %v", c.in, got, zoned, ok)
		}
	}
}
