package platformtag

import (
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
	"testing"
)

func write(t *testing.T, path string, mode os.FileMode) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("x\n"), mode); err != nil {
		t.Fatal(err)
	}
}

func TestHostTarget(t *testing.T) {
	cases := []struct{ system, machine, want string }{
		{"win32", "AMD64", "windows-x86_64"},
		{"darwin", "arm64", "darwin-arm64"},
		{"linux", "aarch64", "linux-arm64"},
		{"linux", "x86_64", "linux-x86_64"},
		{"linux", "amd64", "linux-x86_64"}, // Go の GOARCH の綴り
		{"freebsd", "", "freebsd-unknown"},
	}
	for _, c := range cases {
		if got := hostTarget(c.system, c.machine); got != c.want {
			t.Errorf("hostTarget(%q, %q) = %q, want %q", c.system, c.machine, got, c.want)
		}
	}
	if !slicesHasPrefix(HostTarget(), Systems) {
		t.Errorf("HostTarget() = %q", HostTarget())
	}
}

func slicesHasPrefix(s string, prefixes []string) bool {
	for _, p := range prefixes {
		if strings.HasPrefix(s, p+"-") {
			return true
		}
	}
	return false
}

func TestRunnableTargets(t *testing.T) {
	// arm64 が x86_64 に回るのは変換して動かせる macOS と Windows だけ。
	cases := map[string][]string{
		"darwin-arm64":  {"darwin-arm64", "darwin-x86_64"},
		"windows-arm64": {"windows-arm64", "windows-x86_64"},
		"linux-arm64":   {"linux-arm64"},
	}
	for host, want := range cases {
		if got := runnableTargets(host); !reflect.DeepEqual(got, want) {
			t.Errorf("runnableTargets(%q) = %q", host, got)
		}
	}
}

// PATH の ccnavi は sh（ccnavi_launcher_path）と同じ規則で探す。
func TestPathExecutable(t *testing.T) {
	dir := t.TempDir()
	place := func(name string, mode os.FileMode) string {
		write(t, filepath.Join(dir, name, "ccnavi"), mode)
		return filepath.Join(dir, name)
	}
	first, second := place("a", 0o755), place("b", 0o755)
	list := string(filepath.ListSeparator)
	if got := pathExecutable(first+list+second, ""); got != filepath.Join(first, "ccnavi") {
		t.Errorf("先の項目 = %q", got)
	}
	// 空と相対の項目は cwd で答えが変わるので飛ばす。
	t.Chdir(dir)
	if got := pathExecutable(strings.Join([]string{"a", "", ".", filepath.Join("..", "a")}, list), ""); got != "" {
		t.Errorf("相対の項目 = %q", got)
	}
	if got := pathExecutable("", ""); got != "" {
		t.Errorf("空の PATH = %q", got)
	}
	if runtime.GOOS != "windows" {
		if got := pathExecutable(place("plain", 0o644), ""); got != "" {
			t.Errorf("実行できないファイル = %q", got)
		}
	}

	// 振り分けの sh へのリンクは飛ばして次へ進む（起動すると自分に戻る）。
	launcher := filepath.Join(dir, "scripts", LauncherName)
	write(t, launcher, 0o755)
	linked := filepath.Join(dir, "linked")
	os.MkdirAll(linked, 0o755)
	if err := os.Symlink(launcher, filepath.Join(linked, "ccnavi")); err != nil {
		t.Skipf("シンボリックリンクを作れない: %v", err)
	}
	real := place("real", 0o755)
	if got := pathExecutable(linked+list+real, launcher); got != filepath.Join(real, "ccnavi") {
		t.Errorf("sh へのリンクを飛ばさない: %q", got)
	}
	if got := pathExecutable(linked+list+real, ""); got != filepath.Join(linked, "ccnavi") {
		t.Errorf("skip が無いのに飛ばした: %q", got)
	}
}

func TestLaunchedExecutable(t *testing.T) {
	dir := t.TempDir()
	scripts := filepath.Join(dir, ".ccnavi", "scripts")
	bin := filepath.Join(dir, ".ccnavi", "bin")
	launcher := filepath.Join(scripts, LauncherName)
	write(t, launcher, 0o755)
	write(t, filepath.Join(bin, "darwin-x86_64", "ccnavi"), 0o755)
	write(t, filepath.Join(bin, "darwin-arm64", "ccnavi"), 0o755)
	write(t, filepath.Join(bin, "windows-x86_64", "ccnavi.exe"), 0o755)
	// sh が起動しない隣の置き場は見ない。
	write(t, filepath.Join(scripts, "linux-x86_64", "ccnavi"), 0o755)

	cases := []struct{ launcher, host, want string }{
		{launcher, "darwin-arm64", filepath.Join(bin, "darwin-arm64", "ccnavi")},
		{launcher, "windows-arm64", filepath.Join(bin, "windows-x86_64", "ccnavi.exe")},
		{launcher, "linux-x86_64", ""},
		// 名前が違えば実行ファイルそのものを指しているので探さない。
		{filepath.Join(scripts, "other.sh"), "darwin-arm64", ""},
	}
	for _, c := range cases {
		if got := launchedExecutable(c.launcher, c.host); got != c.want {
			t.Errorf("launchedExecutable(%q, %q) = %q, want %q", c.launcher, c.host, got, c.want)
		}
	}
	if !IsLauncher(launcher) || IsLauncher(filepath.Join(scripts, "ccnavi")) {
		t.Error("IsLauncher の答えが違う")
	}
}
