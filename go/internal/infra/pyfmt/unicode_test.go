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

// 期待は Python の unicodedata.normalize("NFD", s) と unicodedata.combining(c)。
func TestNFDAndCombining(t *testing.T) {
	for in, want := range map[string]string{
		"é": "é", "Å": "Å", "が": "が", "ﬁ": "ﬁ", "한": "한",
	} {
		if got := NFD(in); got != want {
			t.Errorf("NFD(%q) = %q, want %q", in, got, want)
		}
	}
	for r, want := range map[rune]int{
		'a': 0, 'A': 0, '́': 230, '̈': 230, 'ְ': 10, '़': 7, '゙': 8, 'ͅ': 240,
	} {
		if got := Combining(r); got != want {
			t.Errorf("Combining(%U) = %d, want %d", r, got, want)
		}
	}
}
