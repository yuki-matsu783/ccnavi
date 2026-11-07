package diaglog

import (
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/ospath"
)

// head は時刻・レベル・出どころ[pid] の頭。
var head = regexp.MustCompile(`^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}) (DEBUG|INFO |WARN |ERROR) (\S+)\[(\d+)\] `)

func setup(t *testing.T) string {
	t.Helper()
	t.Setenv(env.LogLevel, "")
	t.Setenv(env.ClaudeProjectDir, "")
	return t.TempDir()
}

func logPath(root, name string) string { return filepath.Join(root, "logs", "diag", name+".log") }

func lines(t *testing.T, root string) []string {
	t.Helper()
	data, err := os.ReadFile(logPath(root, "probe"))
	if err != nil {
		return nil
	}
	return strings.Split(strings.TrimSuffix(string(data), "\n"), "\n")
}

func TestLineShape(t *testing.T) {
	root := setup(t)
	Get("probe", root).Info("push を拒否した", "reason", "unapproved", "ticket", "T-12")
	got := lines(t, root)
	if len(got) != 1 {
		t.Fatalf("lines = %q", got)
	}
	m := head.FindStringSubmatch(got[0])
	if m == nil || m[2] != "INFO " || m[3] != "probe" || m[4] != strconv.Itoa(os.Getpid()) {
		t.Fatalf("line = %q", got[0])
	}
	if rest := got[0][len(m[0]):]; rest != "push を拒否した reason=unapproved ticket=T-12" {
		t.Errorf("rest = %q", rest)
	}
	// 値は文字にする。真偽は true / false、nil は空。
	Get("probe", root).Info("m", "a", true, "b", false, "c", nil, "d", 3)
	if got := lines(t, root)[1]; !strings.HasSuffix(got, " m a=true b=false c= d=3") {
		t.Errorf("line = %q", got)
	}
}

func TestStamp(t *testing.T) {
	moment := time.Date(2026, 9, 27, 10, 15, 3, 999_000_000, time.FixedZone("", 9*3600))
	if got := stamp(moment); got != "2026-09-27T10:15:03+09:00" {
		t.Errorf("stamp = %q", got)
	}
	if got := stamp(time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)); got != "2026-01-01T00:00:00+00:00" {
		t.Errorf("stamp = %q", got)
	}
}

func TestQuote(t *testing.T) {
	cases := []struct{ in, want string }{
		{"abc", "abc"}, {`a\b`, `a\b`}, {"", ""},
		{"a b", `"a b"`}, {"a\tb", "\"a\tb\""}, {"k=v", `"k=v"`},
		{`say "hi"\x`, `"say \"hi\"\\x"`},
		{"l1\nl2\r\nl3\rl4", `"l1\nl2\nl3\nl4"`},
	}
	for _, c := range cases {
		if got := quote(c.in); got != c.want {
			t.Errorf("quote(%q) = %q, want %q", c.in, got, c.want)
		}
	}
	if got := fold("1 行目\n2 行目"); got != `1 行目\n2 行目` {
		t.Errorf("fold = %q", got)
	}
}

func TestLevels(t *testing.T) {
	cases := []struct {
		level string
		want  []string
	}{
		{"", []string{"INFO ", "WARN ", "ERROR"}},
		{"debug", []string{"DEBUG", "INFO ", "WARN ", "ERROR"}}, // 大文字小文字は問わない
		{"verbose", []string{"INFO ", "WARN ", "ERROR"}},        // 読めない値は INFO
		{"ERROR", []string{"ERROR"}},
	}
	for _, c := range cases {
		root := setup(t)
		t.Setenv(env.LogLevel, c.level)
		log := Get("probe", root)
		for _, write := range []func(string, ...any){log.Debug, log.Info, log.Warn, log.Error} {
			write("x")
		}
		var got []string
		for _, line := range lines(t, root) {
			got = append(got, head.FindStringSubmatch(line)[2])
		}
		if strings.Join(got, ",") != strings.Join(c.want, ",") {
			t.Errorf("%q: levels = %q, want %q", c.level, got, c.want)
		}
	}
}

func TestPlace(t *testing.T) {
	root := setup(t)
	log := Get("probe", root)
	log.Info("1")
	log.Info("2")
	if got := lines(t, root); len(got) != 2 || !strings.HasSuffix(got[0], "] 1") || !strings.HasSuffix(got[1], "] 2") {
		t.Errorf("lines = %q", got)
	}
	// root が無ければ CLAUDE_PROJECT_DIR。
	other := setup(t)
	t.Setenv(env.ClaudeProjectDir, other)
	Get("probe", "").Info("x")
	if len(lines(t, other)) != 1 {
		t.Error("CLAUDE_PROJECT_DIR に書いていない")
	}
	// どちらも無ければ書かない。
	t.Setenv(env.ClaudeProjectDir, "")
	t.Chdir(setup(t))
	Get("probe", "").Info("x")
	if _, err := os.Stat("logs"); err == nil {
		t.Error("cwd に書いた")
	}
}

func TestUnwritablePlaceIsDropped(t *testing.T) {
	root := setup(t)
	if err := os.WriteFile(filepath.Join(root, "logs"), []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	Get("probe", root).Error("x", "a", "b") // 何も起きず、落ちない
}

func TestRedacted(t *testing.T) {
	root := setup(t)
	token := "ghp_" + strings.Repeat("A", 36)
	Get("probe", root).Info("token "+token, "url", "https://u:"+token+"@h/x")
	got := lines(t, root)
	if len(got) != 1 || strings.Contains(got[0], token) {
		t.Errorf("lines = %q", got)
	}
}

// 3 つの logger で 1 字も違わない伏せ方（tests/sh/test_diaglog_sh.py も同じ入力を比べる）。
func TestMaskUserinfo(t *testing.T) {
	cases := map[string]string{
		"https://user:tok@host/x y":           "https://***@host/x y",
		"oauth2:tok@gitlab.example:org/r.git": "***@gitlab.example:org/r.git",
		"git@github.com:org/r.git":            "git@github.com:org/r.git",
		"  a://b://c@d/e\tu:p@h:x\nz":         "  a://b://***@d/e\t***@h:x\nz",
		"no at":                               "no at",
		"x@y":                                 "x@y",
		"@a:b":                                "@a:b",
		"https://@h":                          "https://***@h",
		"ssh://git@h:22/p https://a:b@c@d/p":  "ssh://***@h:22/p https://***@d/p",
		"u:p@h:c\r\n":                         "***@h:c\r\n",
	}
	for in, want := range cases {
		if got := maskUserinfo(in); got != want {
			t.Errorf("MaskUserinfo(%q) = %q, want %q", in, got, want)
		}
	}
}

func link(t *testing.T, target, path string) {
	t.Helper()
	if err := os.Symlink(target, path); err != nil {
		t.Skipf("リンクを作れない: %v", err)
	}
}

func victim(t *testing.T, root string) string {
	t.Helper()
	path := filepath.Join(root, "logs", "decisions.jsonl")
	os.MkdirAll(filepath.Dir(path), 0o777)
	os.WriteFile(path, []byte("{}\n"), 0o600)
	return path
}

func untouched(t *testing.T, path string) {
	t.Helper()
	if data, _ := os.ReadFile(path); string(data) != "{}\n" {
		t.Errorf("%s = %q", path, data)
	}
}

// リンクを辿ると置き場の外（判定の記録など）を書き換える。
func TestLinksAreNotFollowed(t *testing.T) {
	root := setup(t)
	v := victim(t, root)
	os.MkdirAll(filepath.Join(root, "logs", "diag"), 0o777)
	link(t, "../decisions.jsonl", logPath(root, "probe"))
	Get("probe", root).Error("x")
	untouched(t, v)

	root = setup(t)
	outside := t.TempDir()
	os.MkdirAll(filepath.Join(root, "logs"), 0o777)
	link(t, outside, filepath.Join(root, "logs", "diag"))
	Get("probe", root).Error("x")
	if names, _ := os.ReadDir(outside); len(names) != 0 {
		t.Errorf("wrote %v", names)
	}

	root = setup(t)
	outside = t.TempDir()
	os.MkdirAll(filepath.Join(outside, "diag"), 0o777)
	link(t, outside, filepath.Join(root, "logs"))
	Get("probe", root).Error("x")
	if names, _ := os.ReadDir(filepath.Join(outside, "diag")); len(names) != 0 {
		t.Errorf("wrote %v", names)
	}
}

func TestOtherCharactersInTheNameAreNotWritten(t *testing.T) {
	for _, name := range []string{"../escape", "a.b", "a b", "a/b", ""} {
		root := setup(t)
		Get(name, root).Error("x")
		for _, p := range []string{"logs", "escape.log"} {
			if _, err := os.Stat(filepath.Join(root, p)); err == nil {
				t.Errorf("%q: wrote %s", name, p)
			}
		}
	}
}

func TestNewFileIsOwnerOnly(t *testing.T) {
	if ospath.Windows {
		t.Skip("権限のビットは POSIX だけ")
	}
	root := setup(t)
	Get("probe", root).Info("x")
	info, err := os.Stat(logPath(root, "probe"))
	if err != nil || info.Mode().Perm() != 0o600 {
		t.Errorf("mode = %v, %v", info, err)
	}
}
