package shell

import (
	"errors"
	"slices"
	"strings"
	"testing"
	"time"
)

func TestSplitMatchesShlex(t *testing.T) {
	// 期待は Python の shlex.split（posix・コメント無し）の答え。nil は ValueError。
	cases := []struct {
		src  string
		want []string
	}{
		{"a b  c", []string{"a", "b", "c"}},
		{"", []string{}},
		{"  ", []string{}},
		{`FOO=1 env git commit -m "x y"`, []string{"FOO=1", "env", "git", "commit", "-m", "x y"}},
		{"a'b c'd", []string{"ab cd"}},
		{`a\ b`, []string{"a b"}},
		{`"a\"b"`, []string{`a"b`}},
		{`"a\$b"`, []string{`a\$b`}},
		{`'a\b'`, []string{`a\b`}},
		{`""`, []string{""}},
		{"''", []string{""}},
		// Read と違い、区切りの記号では割らない。# もコメントにしない。
		{"a;b|c", []string{"a;b|c"}},
		{"x # y", []string{"x", "#", "y"}},
		{"日本 語", []string{"日本", "語"}},
		{`"a`, nil},
		{"'a", nil},
		{`a\`, nil},
	}
	for _, c := range cases {
		got, err := Split(c.src)
		if c.want == nil {
			if !errors.Is(err, ErrSplit) {
				t.Errorf("Split(%q) = %q, %v, want ErrSplit", c.src, got, err)
			}
			continue
		}
		if err != nil || len(got) != len(c.want) || (len(got) > 0 && !slices.Equal(got, c.want)) {
			t.Errorf("Split(%q) = %q, %v, want %q", c.src, got, err, c.want)
		}
	}
}

func TestScript(t *testing.T) {
	if _, err := Script([]string{"-c", "true"}, "", nil, time.Second); errors.Is(err, ErrNoShell) {
		t.Skip("sh が無い")
	}
	cases := []struct {
		args     []string
		env      []string
		code     int
		out, err string
		timedOut bool
	}{
		{[]string{"-c", `printf "%s" "$X"`}, []string{"X=1"}, 0, "1", "", false},
		{[]string{"-c", "echo e >&2; exit 3"}, nil, 3, "", "e\n", false},
		{[]string{"-n", "-c", "if"}, nil, 2, "", "", false},
		{[]string{"-c", "sleep 5"}, nil, 0, "", "", true},
	}
	for _, c := range cases {
		ran, err := Script(c.args, "", c.env, 300*time.Millisecond)
		if err != nil || ran.TimedOut != c.timedOut || ran.Shell == "" {
			t.Errorf("%q: %+v, %v", c.args, ran, err)
			continue
		}
		if c.timedOut {
			continue
		}
		if (c.code == 0) != (ran.Code == 0) || (c.code != 2 && ran.Code != c.code) || string(ran.Stdout) != c.out ||
			(c.code != 2 && string(ran.Stderr) != c.err) {
			t.Errorf("%q: %+v", c.args, ran)
		}
	}
	if _, err := Script(nil, "/nonexistent-dir-for-test", nil, time.Second); err == nil || strings.Contains(err.Error(), ErrNoShell.Error()) {
		t.Errorf("無い作業ディレクトリは起こせない: %v", err)
	}
}
