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
	// バイト列は UTF-8 でなくてもそのまま要約する。nil は空と同じ。
	bytesCases := []struct {
		b    []byte
		want string
	}{
		{[]byte{0xff, 0x00, 'a'}, "f9789675a25a87605b0d60387568e25cda7b568653ecdc42e9248588dc70acd5"},
		{nil, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"},
	}
	for _, c := range bytesCases {
		if got := HexBytes(SHA256, c.b, 0); got != c.want {
			t.Errorf("HexBytes(%v) = %q, want %q", c.b, got, c.want)
		}
	}
	h := New(SHA256)
	h.Write([]byte("a"))
	h.Write([]byte("bc"))
	if got := HexSum(h); got[:10] != "ba7816bf8f" {
		t.Errorf("塊に分けて書いても同じ要約: %q", got)
	}
}

func TestRandomHex(t *testing.T) {
	a, b := RandomHex(UUIDBytes), RandomHex(UUIDBytes)
	if len(a) != 32 || a == b {
		t.Errorf("RandomHex = %q, %q", a, b)
	}
}
