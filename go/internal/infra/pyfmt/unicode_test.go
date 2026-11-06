package pyfmt

import "testing"

// Casefold は区切りを変えない（Windows でも `/` のまま）。NFKC は全角の英数字と合字を素の字にする。
func TestCasefoldAndNormalize(t *testing.T) {
	for _, c := range []struct{ got, want string }{
		{Casefold("Straße/ﬁ\\X"), "strasse/fi\\x"},
		{NFKC("ＣＣＮＡＶＩ　ｐｒｕｎｅ ﬁ"), "CCNAVI prune fi"},
		{NFC("が"), "が"},
	} {
		if c.got != c.want {
			t.Errorf("%q, want %q", c.got, c.want)
		}
	}
}
