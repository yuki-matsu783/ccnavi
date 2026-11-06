package pathmatch

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type linkFixture struct {
	tmp, root, junction string
}

func touch(t *testing.T, path string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
}

func symlink(t *testing.T, target, link string) {
	t.Helper()
	if err := os.Symlink(target, link); err != nil {
		t.Skipf("シンボリックリンクを作れない: %v", err)
	}
}

// newLinkFixture は表の場面をそろえる。junction は Linux では作れないので isJunction を差し替える。
func newLinkFixture(t *testing.T) linkFixture {
	tmp, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	root := filepath.Join(tmp, "root")
	touch(t, filepath.Join(root, "real", "file.txt"))
	touch(t, filepath.Join(root, "plain", "sub", "file.txt"))
	touch(t, filepath.Join(root, "junc", "inner", "f"))
	if err := os.MkdirAll(filepath.Join(tmp, "outside"), 0o755); err != nil {
		t.Fatal(err)
	}
	symlink(t, filepath.Join(root, "real"), filepath.Join(root, "dirlink"))
	symlink(t, filepath.Join(tmp, "outside"), filepath.Join(root, "outer"))
	symlink(t, "file.txt", filepath.Join(root, "real", "filelink"))
	symlink(t, root, filepath.Join(root, "selfdir"))
	symlink(t, root, filepath.Join(tmp, "rootlink"))
	f := linkFixture{tmp: tmp, root: root, junction: filepath.Join(root, "junc")}
	saved := isJunction
	isJunction = func(p string) bool { return p == f.junction }
	t.Cleanup(func() { isJunction = saved })
	return f
}

func split(rel string) []string {
	var parts []string
	for _, p := range strings.Split(rel, "/") {
		if p != "" {
			parts = append(parts, p)
		}
	}
	return parts
}

func TestLinksTable(t *testing.T) {
	f := newLinkFixture(t)
	roots := map[string]string{
		"root":     f.root,
		"rootlink": filepath.Join(f.tmp, "rootlink"),
		// 大文字小文字だけ違う root も、どの機械でも同じ場所として扱う。
		"upper": filepath.Join(f.tmp, "ROOT"),
	}
	rows := []struct {
		name, root, rel string
		linked, mid     bool // 道（最後を含む）にリンクがあるか、最後を除いた道にあるか
	}{
		{"リンクなし", "root", "plain/sub/file.txt", false, false},
		{"途中のディレクトリがリンク", "root", "dirlink/file.txt", true, true},
		{"最後の名前がリンク", "root", "real/filelink", true, false},
		{"root を指す子のリンク", "root", "selfdir/plain", true, true},
		{"root の外を指すリンク", "root", "outer/x", true, true},
		{"root 自身", "root", "", false, false},
		{"存在しない", "root", "plain/nope/x", false, false},
		{"root 自身がリンク", "rootlink", "plain/sub/file.txt", false, false},
		{"大文字小文字違いの root", "upper", "plain/sub/file.txt", false, false},
		{"途中が junction", "root", "junc/inner/f", true, true},
	}
	for _, r := range rows {
		root := roots[r.root]
		parts := split(r.rel)
		path := filepath.Join(append([]string{root}, parts...)...)
		if got := HasLink(root, path); got != r.linked {
			t.Errorf("%s: HasLink = %v, want %v", r.name, got, r.linked)
		}
		if _, got := FirstLink(root, parts, true); got != r.linked {
			t.Errorf("%s: FirstLink = %v, want %v", r.name, got, r.linked)
		}
		if _, got := FirstLink(root, parts, false); got != r.mid {
			t.Errorf("%s: FirstLink(最後を除く) = %v, want %v", r.name, got, r.mid)
		}
	}
}

func TestFirstLinkNamesTheLink(t *testing.T) {
	f := newLinkFixture(t)
	cases := []struct {
		parts []string
		want  string
	}{
		{[]string{"real", "filelink"}, filepath.Join(f.root, "real", "filelink")},
		{[]string{"dirlink", "file.txt"}, filepath.Join(f.root, "dirlink")},
		{[]string{"junc", "inner"}, f.junction},
	}
	for _, c := range cases {
		if got, _ := FirstLink(f.root, c.parts, true); got != c.want {
			t.Errorf("FirstLink(%v) = %q, want %q", c.parts, got, c.want)
		}
	}
	// 途中が無ければ、その先のリンクは見ない。
	if state, _ := Scan(f.root, []string{"nope", "dirlink"}, true); state != Missing {
		t.Errorf("Scan(無い途中) = %v, want Missing", state)
	}
	if state, _ := Scan(f.root, []string{"plain", "sub"}, true); state != Clean {
		t.Errorf("Scan(リンクなし) = %v, want Clean", state)
	}
}

func TestHasLinkSpellings(t *testing.T) {
	f := newLinkFixture(t)
	// root より上のリンクを通った綴り（/tmp と /private/tmp）は、実体が root に着けば止まる。
	parent := filepath.Join(f.tmp, "parent")
	ws := filepath.Join(parent, "ws")
	touch(t, filepath.Join(ws, "a", "b"))
	symlink(t, parent, filepath.Join(f.tmp, "viaparent"))
	if HasLink(ws, filepath.Join(f.tmp, "viaparent", "ws", "a", "b")) {
		t.Error("root の別綴りがリンクと数えられた")
	}
	// root から親へ出て root の中へ戻るリンクは数える。
	symlink(t, f.tmp, filepath.Join(f.root, "up"))
	if !HasLink(f.root, filepath.Join(f.root, "up", "root", "real", "file.txt")) {
		t.Error("中へ戻るリンクを見逃した")
	}
	// root へのリンクを通った綴りは、そのリンクがあるので真。
	alias := filepath.Join(f.tmp, "rootlink")
	if !HasLink(f.root, filepath.Join(alias, "plain")) {
		t.Error("root へのリンクを通った綴りを見逃した")
	}
	if HasLink(alias, filepath.Join(alias, "plain")) {
		t.Error("root 自身がリンクなのを数えた")
	}
	// root の外と、解けないパスは拒否する側。
	for _, outside := range []string{filepath.Join(f.tmp, "outside", "x"), f.tmp, f.root + "\x00x"} {
		if !HasLink(f.root, outside) {
			t.Errorf("HasLink(%q) = false, want true", outside)
		}
	}
}

func TestEscapes(t *testing.T) {
	f := newLinkFixture(t)
	cases := []struct {
		root, path string
		want       bool
	}{
		{f.root, filepath.Join(f.root, "plain"), false},
		{f.root, f.root, false},
		{f.root, filepath.Join(f.root, "dirlink"), false},
		{f.root, filepath.Join(f.root, "outer"), true},
		{f.root, filepath.Join(f.tmp, "outside"), true},
		{filepath.Join(f.tmp, "ROOT"), filepath.Join(f.root, "plain"), false},
		{f.root, f.root + "\x00", true},
	}
	for _, c := range cases {
		if got := Escapes(c.root, c.path); got != c.want {
			t.Errorf("Escapes(%q, %q) = %v, want %v", c.root, c.path, got, c.want)
		}
	}
}

func TestIsLink(t *testing.T) {
	f := newLinkFixture(t)
	cases := map[string]bool{
		filepath.Join(f.root, "dirlink"): true,
		f.junction:                       true,
		filepath.Join(f.root, "plain"):   false,
		filepath.Join(f.root, "missing"): false,
	}
	for path, want := range cases {
		if got := IsLink(path); got != want {
			t.Errorf("IsLink(%q) = %v, want %v", path, got, want)
		}
	}
}
