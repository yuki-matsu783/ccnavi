package digest

import (
	"strings"
	"testing"
)

// Python の hashlib の答え（記録のキーが揃うこと）。
func TestHexMatchesHashlib(t *testing.T) {
	cases := []struct {
		algo Algo
		text string
		n    int
		want string
	}{
		{SHA1, "abc", 16, "a9993e364706816a"},
		{SHA256, "abc", 8, "ba7816bf"},
		{SHA256, "日本", 8, "cf2abf0c"},
		{SHA1, "", 0, "da39a3ee5e6b4b0d3255bfef95601890afd80709"},
	}
	for _, c := range cases {
		if got := Hex(c.algo, c.text, c.n); got != c.want {
			t.Errorf("Hex(%v, %q, %d) = %q, want %q", c.algo, c.text, c.n, got, c.want)
		}
	}
	if got, err := HexOf(SHA256, strings.NewReader("abc")); err != nil || got != Hex(SHA256, "abc", 0) {
		t.Errorf("HexOf = %q, %v", got, err)
	}
}

func TestRandomHex(t *testing.T) {
	a, b := RandomHex(UUIDBytes), RandomHex(UUIDBytes)
	if len(a) != 32 || a == b {
		t.Errorf("RandomHex = %q, %q", a, b)
	}
}
