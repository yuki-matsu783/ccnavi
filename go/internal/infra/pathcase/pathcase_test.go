package pathcase

import (
	"os"
	"path/filepath"
	"testing"
)

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

func TestHasExt(t *testing.T) {
	for name, want := range map[string]bool{"a.md": true, "A.MD": true, "a.mdx": false, "md": false, ".md": true} {
		if got := HasExt(name, ".md"); got != want {
			t.Errorf("HasExt(%q) = %v", name, got)
		}
	}
}

func TestSameNorm(t *testing.T) {
	cases := []struct {
		a, b string
		want bool
	}{
		{"a/./B", "A/b", true},
		{"a/../b", "B", true},
		{"a/b/", "a/b", true},
		{"a/b", "a/c", false},
		{"Straße", "STRASSE", true},
	}
	for _, c := range cases {
		if got := SameNorm(c.a, c.b); got != c.want {
			t.Errorf("SameNorm(%q, %q) = %v", c.a, c.b, got)
		}
	}
}

func TestSameReal(t *testing.T) {
	dir := t.TempDir()
	real := filepath.Join(dir, "Real")
	if err := os.Mkdir(real, 0o755); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(dir, "link")
	if err := os.Symlink(real, link); err != nil {
		t.Skip("リンクを作れない")
	}
	if !SameReal(link, filepath.Join(dir, "REAL")) || Real(link) != Real(real) {
		t.Error("リンクは辿り、大文字小文字は区別しない")
	}
	if SameReal(link, dir) {
		t.Error("別の場所は違う")
	}
	if SameNorm(link, real) {
		t.Error("SameNorm はリンクを辿らない")
	}
}

// ツリーの中かは部分ごとに、大文字小文字を区別せずに見る。".." で始まる名前は中。相対は path の綴りのまま。
func TestUnder(t *testing.T) {
	base := filepath.Join(string(filepath.Separator)+"ws", "Tree")
	for _, c := range []struct {
		path, rel string
		ok        bool
	}{
		{filepath.Join(base, "a", "B.md"), filepath.Join("a", "B.md"), true},
		{filepath.Join(string(filepath.Separator)+"WS", "tree", "X"), "X", true},
		{filepath.Join(base, "..foo"), "..foo", true},
		{base, ".", true},
		{base + string(filepath.Separator), ".", true},
		{filepath.Join(base, "..", "x"), "", false},
		{base + "x", "", false},
		{filepath.Join(string(filepath.Separator)+"ws", "STRASSE", "Straße"), "", false},
	} {
		rel, ok := Under(c.path, base)
		if rel != c.rel || ok != c.ok {
			t.Errorf("Under(%q) = %q, %v", c.path, rel, ok)
		}
	}
	if rel, ok := Under(filepath.Join(string(filepath.Separator)+"x", "STRASSE", "Ä"), filepath.Join(string(filepath.Separator)+"x", "straße")); rel != "Ä" || !ok {
		t.Errorf("畳むと長さの変わる字: %q, %v", rel, ok)
	}
}
