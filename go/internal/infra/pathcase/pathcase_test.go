package pathcase

import "testing"

func TestFold(t *testing.T) {
	cases := map[string]string{
		"ABC/Def": "abc/def",
		"Straße":  "strasse",
		"ﬁle.TXT": "file.txt",
		"already": "already",
	}
	for in, want := range cases {
		if got := Fold(in); got != want {
			t.Errorf("Fold(%q) = %q, want %q", in, got, want)
		}
	}
}
