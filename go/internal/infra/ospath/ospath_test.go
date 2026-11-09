package ospath

import (
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"slices"
	"testing"
)

// 期待値は Python 3.13 の posixpath の答え。

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

// 回帰: cwd がリンクを通って入った場所でも、相対パスは実体のパスから作る。
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

// 期待値は Python 3.13 の posixpath.splitext と ntpath.splitext。
func TestSplitext(t *testing.T) {
	cases := []struct{ in, root, ext, winRoot, winExt string }{
		{"a.txt", "a", ".txt", "a", ".txt"},
		{"/x/a.b.c", "/x/a.b", ".c", "/x/a.b", ".c"},
		{".bashrc", ".bashrc", "", ".bashrc", ""},
		{"..bashrc", "..bashrc", "", "..bashrc", ""},
		{"a.", "a", ".", "a", "."},
		{"/x.y/z", "/x.y/z", "", "/x.y/z", ""},
		{"x.y/", "x.y/", "", "x.y/", ""},
		{"...", "...", "", "...", ""},
		{"", "", "", "", ""},
		{".a.b", ".a", ".b", ".a", ".b"},
		{"a/.b", "a/.b", "", "a/.b", ""},
		{`a\b.c\d`, `a\b`, `.c\d`, `a\b.c\d`, ""},
	}
	for _, c := range cases {
		root, ext := c.root, c.ext
		if Windows {
			root, ext = c.winRoot, c.winExt
		}
		if gotRoot, gotExt := Splitext(c.in); gotRoot != root || gotExt != ext {
			t.Errorf("Splitext(%q) = %q %q, want %q %q", c.in, gotRoot, gotExt, root, ext)
		}
	}
}

func TestDiskListdirAndIsLink(t *testing.T) {
	dir := t.TempDir()
	for _, name := range []string{"b", "a", "C"} {
		if err := os.WriteFile(filepath.Join(dir, name), nil, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	// sorted(os.listdir(dir)) と同じく、コード位置の順
	if got, err := DiskListdir(dir); err != nil || !reflect.DeepEqual(got, []string{"C", "a", "b"}) {
		t.Errorf("DiskListdir = %v, %v", got, err)
	}
	if _, err := DiskListdir(filepath.Join(dir, "none")); err == nil {
		t.Error("無いディレクトリを読めた")
	}
	if IsLink(filepath.Join(dir, "a")) || IsLink(filepath.Join(dir, "none")) {
		t.Error("リンクでないものをリンクと言った")
	}
	link := filepath.Join(dir, "link")
	if err := os.Symlink(filepath.Join(dir, "none"), link); err != nil {
		t.Skip("リンクを作れない:", err)
	}
	if !IsLink(link) {
		t.Error("壊れたリンクを見落とした")
	}
}

func TestStat(t *testing.T) {
	path := filepath.Join(t.TempDir(), "f")
	if err := os.WriteFile(path, []byte("abc"), 0o644); err != nil {
		t.Fatal(err)
	}
	if mtime, size, ok := Stat(path); !ok || size != 3 || mtime.IsZero() {
		t.Errorf("Stat = %v, %d, %v", mtime, size, ok)
	}
	if _, _, ok := Stat(path + "x"); ok {
		t.Error("無いファイルで ok")
	}
}

// 字は符号位置で数える（日: の 2 字目は ":"）。
func TestHasDrive(t *testing.T) {
	cases := []struct {
		path        string
		letter, any bool
	}{
		{`C:\x`, true, true},
		{"c:", true, true},
		{"1:", false, true},
		{"日:x", false, true},
		{"C", false, false},
		{"", false, false},
		{"/C:", false, false},
	}
	for _, c := range cases {
		if got := HasDrive(c.path, true); got != c.letter {
			t.Errorf("HasDrive(%q, true) = %v", c.path, got)
		}
		if got := HasDrive(c.path, false); got != c.any {
			t.Errorf("HasDrive(%q, false) = %v", c.path, got)
		}
	}
	if n, ok := HardLinks(t.TempDir()); !ok || n == 0 {
		t.Errorf("HardLinks = %d, %v", n, ok)
	}
}

// 期待は POSIX の relpath の答え。相対にできない別のドライブは Windows でしか起きない。
func TestRelOr(t *testing.T) {
	cases := []struct{ path, start, want string }{
		{"/a/b/c", "/a", "b/c"},
		{"/a", "/a", "."},
		{"/a/x", "/a/b", "../x"},
	}
	for _, c := range cases {
		if Windows {
			continue
		}
		if got := RelOr(c.path, c.start); got != c.want {
			t.Errorf("RelOr(%q, %q) = %q, want %q", c.path, c.start, got, c.want)
		}
	}
	if Windows {
		if got := RelOr(`D:\x`, `C:\y`); got != `D:\x` {
			t.Errorf("別のドライブは path のまま: %q", got)
		}
	}
}

// 期待は区切りを残さない分け方と、残す分け方の答え。
func TestSplitlinesKeep(t *testing.T) {
	cases := []struct {
		in         string
		keep, drop []string
	}{
		{"a\r\nb\rc\vd\x1ce f\n", []string{"a\r\n", "b\r", "c\v", "d\x1c", "e ", "f\n"}, []string{"a", "b", "c", "d", "e", "f"}},
		{"", nil, nil},
		{"x", []string{"x"}, []string{"x"}},
		{"\n\n", []string{"\n", "\n"}, []string{"", ""}},
		{"a\r", []string{"a\r"}, []string{"a"}},
		{"\r\n\r", []string{"\r\n", "\r"}, []string{"", ""}},
	}
	for _, c := range cases {
		if got := SplitlinesKeep(c.in); !reflect.DeepEqual(got, c.keep) {
			t.Errorf("SplitlinesKeep(%q) = %q, want %q", c.in, got, c.keep)
		}
		if got := Splitlines(c.in); !reflect.DeepEqual(got, c.drop) {
			t.Errorf("Splitlines(%q) = %q, want %q", c.in, got, c.drop)
		}
	}
}

// 中身と更新時刻を戻しても、変更時刻（ctime）か inode が変わる。リンクは辿らない。
func TestIdentity(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "a")
	if err := os.WriteFile(path, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	ino, ctime, ok := Identity(path)
	if !ok || ino == 0 || ctime == 0 {
		t.Fatalf("Identity = %d, %d, %v", ino, ctime, ok)
	}
	link := filepath.Join(dir, "link")
	if err := os.Symlink(path, link); err == nil {
		if other, _, ok := Identity(link); !ok || other == ino {
			t.Errorf("リンクそのものを読む: %d, %v", other, ok)
		}
	}
	if _, _, ok := Identity(filepath.Join(dir, "missing")); ok {
		t.Error("無いものは読めない")
	}
}

func TestWithoutEnv(t *testing.T) {
	got := WithoutEnv([]string{"GIT_DIR=/a", "PATH=/bin", "GIT_WORK_TREE=/b", "X=1=2"}, []string{"GIT_DIR", "GIT_WORK_TREE"})
	want := []string{"PATH=/bin", "X=1=2"}
	if !slices.Equal(got, want) {
		t.Fatalf("got %q, want %q", got, want)
	}
}
