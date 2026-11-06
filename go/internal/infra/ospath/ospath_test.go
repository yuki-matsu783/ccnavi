package ospath

import (
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"testing"
)

// 期待値は Python 3.13 の posixpath で求めたもの。

func TestPosixSpelling(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX の綴りの決まり")
	}
	joins := []struct {
		in   []string
		want string
	}{
		{[]string{"a", ""}, "a/"},
		{[]string{"a", "/b"}, "/b"},
		{[]string{"a/", "b"}, "a/b"},
		{[]string{"", "b"}, "b"},
		{[]string{"/a", "b/", "c"}, "/a/b/c"},
	}
	for _, c := range joins {
		if got := Join(c.in[0], c.in[1:]...); got != c.want {
			t.Errorf("Join(%q) = %q, want %q", c.in, got, c.want)
		}
	}
	dirs := []struct{ in, dir, base string }{
		{"/a/b", "/a", "b"},
		{"/a/b/", "/a/b", ""},
		{"a", "", "a"},
		{"/", "/", ""},
		{"//a", "//", "a"},
		{"a//b//", "a//b", ""},
		{"", "", ""},
		{"///", "///", ""},
	}
	for _, c := range dirs {
		if got := Dirname(c.in); got != c.dir {
			t.Errorf("Dirname(%q) = %q, want %q", c.in, got, c.dir)
		}
		if got := Basename(c.in); got != c.base {
			t.Errorf("Basename(%q) = %q, want %q", c.in, got, c.base)
		}
	}
	norms := map[string]string{
		"": ".", "//a/b": "//a/b", "///a": "/a", "a/../..": "..", "/../a": "/a", "./a/./b/": "a/b", "a/b/../c": "a/c",
	}
	for in, want := range norms {
		if got := Normpath(in); got != want {
			t.Errorf("Normpath(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestRealpath(t *testing.T) {
	d, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(d, "real", "sub"), 0o755); err != nil {
		t.Fatal(err)
	}
	links := [][2]string{{"real", "link"}, {"l2", "l1"}, {"l1", "l2"}, {filepath.Join("..", "real"), filepath.Join("real", "sub", "up")}}
	for _, l := range links {
		if err := os.Symlink(l[0], filepath.Join(d, l[1])); err != nil {
			t.Skipf("シンボリックリンクを作れない: %v", err)
		}
	}
	t.Chdir(d)
	cases := map[string]string{
		"link/missing/x":         filepath.Join(d, "real", "missing", "x"), // 無い名前から先は書かれたまま
		"link/..":                d,                                        // リンクを解いてから上がる
		"l1/x":                   filepath.Join(d, "l1", "x"),              // 輪はそこで解くのをやめる
		"real/sub/up/sub":        filepath.Join(d, "real", "real", "sub"),
		"./link/":                filepath.Join(d, "real"),
		filepath.Join(d, "link"): filepath.Join(d, "real"),
	}
	for in, want := range cases {
		if got := Realpath(in); got != want {
			t.Errorf("Realpath(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestTextHelpers(t *testing.T) {
	got := Splitlines("a\r\nb\rc\vd\x1ce\u2028f\n")
	if want := []string{"a", "b", "c", "d", "e", "f"}; !reflect.DeepEqual(got, want) {
		t.Errorf("Splitlines = %q, want %q", got, want)
	}
	if got := Strip(" \x1c　x\u0085 "); got != "x" {
		t.Errorf("Strip = %q", got)
	}
}

// 回帰: cwd がリンクを通って入った場所でも、相対パスは Python の os.getcwd() と同じく実体から作る。
func TestRelativeFromLinkedCwd(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("シンボリックリンクを作れない環境がある")
	}
	base, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	real := filepath.Join(base, "real")
	link := filepath.Join(base, "link")
	if err := os.Mkdir(real, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(real, link); err != nil {
		t.Fatal(err)
	}
	t.Chdir(link)
	t.Setenv("PWD", link)
	want := filepath.Join(real, "x")
	if got := Abspath("x"); got != want {
		t.Errorf("Abspath = %q, want %q", got, want)
	}
	if got := Realpath("x"); got != want {
		t.Errorf("Realpath = %q, want %q", got, want)
	}
}
