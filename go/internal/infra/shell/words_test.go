package shell

import (
	"slices"
	"testing"
)

func TestTokenizeMatchesShlex(t *testing.T) {
	// 期待は Python の shlex（posix・punctuation_chars・whitespace_split）の答え。
	cases := []struct {
		src  string
		want []string
	}{
		{`a"b c"d`, []string{"ab cd"}},
		{`x\ y`, []string{"x y"}},
		// 二重引用の中で外れるのは `\"` と `\\` だけ。
		{`"a\$b\\"`, []string{`a\$b\`}},
		{"''", []string{""}},
		{"a&&b", []string{"a", "&&", "b"}},
		{"a;;&b", []string{"a", ";;&", "b"}},
		// つながった区切りは割る。リダイレクトは割らない。
		{"(true);x", []string{"(", "true", ")", ";", "x"}},
		{"a 2>&1", []string{"a", "2", ">&", "1"}},
		{"$((1<<2))", nil},
	}
	for _, c := range cases {
		got, err := tokenize(c.src)
		if err != nil || !slices.Equal(got, c.want) {
			t.Errorf("tokenize(%q) = %q, %v, want %q", c.src, got, err, c.want)
		}
	}
	for _, src := range []string{`"a`, "'a", `a\`} {
		if _, err := tokenize(src); err == nil {
			t.Errorf("tokenize(%q) は閉じないので失敗するはず", src)
		}
	}
}

func TestQuoteMatchesShlex(t *testing.T) {
	cases := map[string]string{
		"": "''", "abc": "abc", "/tmp/ws-1.a": "/tmp/ws-1.a", "a b": "'a b'", "it's": `'it'"'"'s'`,
		"/tmp/日本": "'/tmp/日本'", `C:\work\tree`: `'C:\work\tree'`,
	}
	for in, want := range cases {
		if got := Quote(in); got != want {
			t.Errorf("Quote(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestBase(t *testing.T) {
	cases := map[string]string{
		"/usr/bin/git": "git", "git.EXE": "git", `C:\x\git.cmd`: "git", "a.exe.sh": "a.exe.sh",
	}
	for in, want := range cases {
		if got := Base(in); got != want {
			t.Errorf("Base(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestRunnerCommand(t *testing.T) {
	cases := []struct {
		name string
		args []string
		want []string
	}{
		{"command", []string{"-v", "jq"}, nil}, // 探すだけ
		{"timeout", []string{"-s", "KILL", "5", "rm"}, []string{"rm"}},
		{"env", []string{"A=1", "--", "B=2", "rm"}, []string{"rm"}},
		{"sudo", []string{"-Eu", "me", "rm"}, []string{"rm"}},
	}
	for _, c := range cases {
		if got := RunnerCommand(c.name, c.args); !slices.Equal(got, c.want) {
			t.Errorf("RunnerCommand(%q, %q) = %q, want %q", c.name, c.args, got, c.want)
		}
	}
}

func TestRedirectWidth(t *testing.T) {
	// 行き先の語が無い演算子は bash では構文エラーで何も書かれないので、リダイレクトに数えない。
	for _, words := range [][]string{{"echo", "x", ">"}, {"echo", "x", "2", ">"}, {"echo", "x", ">>"}} {
		if got := RedirectWidth(words, len(words)-1); got != 0 {
			t.Errorf("RedirectWidth(%q) = %d", words, got)
		}
	}
	cases := []struct {
		words []string
		want  int
	}{
		{[]string{">", "f"}, 2},
		{[]string{"2", ">&", "1"}, 3},
		{[]string{"{fd}", ">", "f"}, 3},
		// Python の str.isdigit と同じく、上付きの数字も fd の番号と読む。
		{[]string{"²", ">", "f"}, 3},
		{[]string{"x", ">", "f"}, 0},
	}
	for _, c := range cases {
		if got := RedirectWidth(c.words, 0); got != c.want {
			t.Errorf("RedirectWidth(%q) = %d, want %d", c.words, got, c.want)
		}
	}
}

func TestReadsStdin(t *testing.T) {
	cases := []struct {
		words []string
		want  bool
	}{
		{[]string{"sh"}, true},
		{[]string{"sh", "-s", "a"}, true},
		{[]string{"sh", "<", "x"}, true},
		{[]string{"busybox", "sh"}, true},
		{[]string{"sh", "/dev/stdin"}, true},
		{[]string{"env", "-u", "X", "sh"}, true},
		{[]string{"bash", "-euo", "pipefail"}, true},
		{[]string{"bash", "--rcfile", "r"}, true},
		{[]string{"sh", "--", "-"}, true},
		{[]string{"sh", "-c", "x"}, false},
		{[]string{"sh", "x.sh"}, false},
		{[]string{"/BIN/SH.exe"}, false},
	}
	for _, c := range cases {
		if got := ReadsStdin(c.words); got != c.want {
			t.Errorf("ReadsStdin(%q) = %v, want %v", c.words, got, c.want)
		}
	}
}
