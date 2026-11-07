package pyre

import (
	"errors"
	"strings"
	"testing"
)

// ルールと組み込みの保護が頼る Python の答え。写しとの乱数の突き合わせ（python_test.go）と別に、読める形で残す。
func TestSearchTable(t *testing.T) {
	cases := []struct {
		pattern string
		flags   Flag
		subject string
		want    bool
	}{
		{`\bgit\b`, IgnoreCase, "日git", false}, // \w は Unicode（日 は語の字）
		{`\bgit\b`, IgnoreCase, "日 git", true},
		{`(?a)\bgit\b`, IgnoreCase, "日git", true},
		{`^\d$`, 0, "٣", true},  // \d は Unicode の 10 進の数字
		{`\s`, 0, "\x1c", true}, // \s は Python の str.isspace
		{`\s`, 0, "　", true},
		{`a$`, 0, "a\n", true}, // $ は末尾の改行の手前にも当たる
		{`a\Z`, 0, "a\n", false},
		{`(?m)a$\n^b`, 0, "a\nb", true},
		{`.`, 0, "\n", false},
		{`(?s).`, 0, "\n", true},
		{`GIT PUSH`, IgnoreCase, "git push", true},
		{`(?-i:GIT) push`, IgnoreCase, "git PUSH", false},
		{`k`, IgnoreCase, "K", true}, // ケルビン記号
		{`s`, IgnoreCase, "ſ", true},
		{`i`, IgnoreCase, "İ", true}, // İ の小文字は i（Python の単純な小文字）
		{`İ`, IgnoreCase, "i", true},
		{`[a-z]`, IgnoreCase, "İ", true},
		{`[İ]`, IgnoreCase, "I", true},
		{`[^K-k]`, IgnoreCase, "K", false},
		{`(?:[^\s\x00\\/]*+[\\/])*+find`, 0, "/usr/bin/find", true},
		{`a*+a`, 0, "aaa", false}, // 所有的な繰り返しは戻らない
		{`(?>a*)a`, 0, "aaa", false},
		{`(?:a|ab)*c`, 0, "abc", true},
		{`(?:)*x`, 0, "x", true}, // 空の繰り返しで止まらない
		{`(a?)*?b`, 0, "aab", true},
		{`a{,2}b`, 0, "aab", true},
		{`a{2`, 0, "a{2", true},
		{`(a)?(?(1)x|y)`, 0, "ax", true},
		{`(?<=ab)c`, 0, "abc", true},
		{`(?<!ab)c`, 0, "abc", false},
		{`\B`, 0, "", false},
		{`\b`, 0, "", false},
	}
	for _, c := range cases {
		re, err := Compile(c.pattern, c.flags)
		if err != nil {
			t.Errorf("%q: %v", c.pattern, err)
			continue
		}
		if got := re.Search(c.subject); got != c.want {
			t.Errorf("%q.Search(%q) = %v", c.pattern, c.subject, got)
		}
	}
}

func TestErrorsReadLikePython(t *testing.T) {
	cases := map[string]string{
		"(a":               "missing ), unterminated subpattern at position 0",
		"a)":               "unbalanced parenthesis at position 1",
		"a**":              "multiple repeat at position 2",
		"*a":               "nothing to repeat at position 0",
		"[z-a]":            "bad character range z-a at position 1",
		"[a":               "unterminated character set at position 0",
		`\p`:               `bad escape \p at position 0`,
		`\z`:               `bad escape \z at position 0`,
		`a\`:               "bad escape (end of pattern) at position 1",
		`(a)\2`:            "invalid group reference 2 at position 4",
		"(?<=a*)b":         "look-behind requires fixed-width pattern",
		"a(?i)":            "global flags not at the start of the expression at position 1",
		"(?(1)a|b)":        "invalid group reference 1 at position 3",
		"(?P<1>a)":         "bad character in group name '1' at position 4",
		"(?P<a>x)(?P<a>y)": "redefinition of group name 'a' as group 2; was group 1 at position 12",
		"a{3,2}":           "min repeat greater than max repeat at position 2",
		"x\n(":             "missing ), unterminated subpattern at position 2 (line 2, column 1)",
		`\N{EM DASH}`:      "undefined character name 'EM DASH' at position 0",
	}
	for pattern, want := range cases {
		_, err := Compile(pattern, 0)
		if err == nil || err.Error() != want {
			t.Errorf("Compile(%q) = %v, want %q", pattern, err, want)
		}
	}
}

func TestTooDeepAndOverflow(t *testing.T) {
	ok := strings.Repeat("(", maxNesting) + "x" + strings.Repeat(")", maxNesting)
	if _, err := Compile(ok, 0); err != nil {
		t.Errorf("%d 段: %v", maxNesting, err)
	}
	deep := "(" + ok + ")"
	if _, err := Compile(deep, 0); !errors.Is(err, ErrTooDeep) {
		t.Errorf("%d 段: %v", maxNesting+1, err)
	}
	if _, err := Compile("a{4294967295}", 0); !errors.Is(err, ErrOverflow) {
		t.Errorf("%v", err)
	}
	if _, err := Compile("a{4294967294}", 0); err != nil {
		t.Errorf("%v", err)
	}
}

func TestFindAllAndNamedGroups(t *testing.T) {
	re, err := Compile(`x*|(?P<k>b)`, 0)
	if err != nil {
		t.Fatal(err)
	}
	var got []string
	for _, f := range re.FindAll("axxb") {
		k, ok := f.Named("k")
		got = append(got, f.Text()+"/"+k+map[bool]string{true: "+", false: "-"}[ok])
	}
	// 空の一致のすぐ後ろは、空でない一致だけを採る。
	if want := "/- xx/- /- b/b+ /-"; strings.Join(got, " ") != want {
		t.Errorf("%q", strings.Join(got, " "))
	}
	if _, ok := re.Group("k"); !ok {
		t.Error("名前のグループが無い")
	}
}

func TestEscape(t *testing.T) {
	if got := Escape("a.b-c d/日"); got != `a\.b\-c\ d/日` {
		t.Error(got)
	}
}

// ルールの glob は先頭に ^ を付けて当てる（ルールの読み込みと同じ）。式の字面は python_test.go が Python の答えの写しと突き合わせるので、
// ここでは当たり方の境界（全体に当たる・区切りの両方の表記・改行・文字クラス）を確かめる。
func TestTranslateGlobMatches(t *testing.T) {
	cases := []struct {
		glob, subject string
		want          bool
	}{
		// 文字列全体に当たる。部分一致は自動で足さない。
		{"git push", "git push", true},
		{"git push", "cd /repo && git push", false},
		{"*git push*", "cd /repo && git push", true},
		// 区切りはどちらの表記にも当たる。
		{"*/secrets/*", "/repo/secrets/token.txt", true},
		{"*/secrets/*", `C:\repo\secrets\token.txt`, true},
		{"*/secrets/*", "/repo/docs/readme.md", false},
		// 語の切れ目は入らない（仕様）。
		{"*sed*", "sedate --now", true},
		{"*sed *", "sedate --now", false},
		{"*foo*bar*", "foobar", true},
		{"*rm -rf *", "rm -r /tmp/x", false},
		{"*", "a\nb", true},
		{"a?c", "a\nc", true},
		{"?", "é", true},
		{"a.b", "axb", false},
		// 文字クラスは fnmatch と同じ答え。
		{"[!a-c]x", "ax", false},
		{"[!a-c]x", "dx", true},
		{"[]]", "]", true},
		{"[z-a]", "m", false},
		{"[]-a]", "-", false},
		{"[]-a]", "^", true},
		{`[\]`, `\`, true},
		{"[!]", "[!]", true},
		{"[^a]", "^", true},
		{"[^a]", "b", false},
		{"[[:x]", ":", true},
		// クラスの中の "/" も置き換わり、クラスが途中で閉じる（Python と同じ）。
		{"[a/b]", "/", false},
		{"[a/b]", `\b]`, true},
	}
	for _, c := range cases {
		re, err := Compile("^"+TranslateGlob(c.glob), 0)
		if err != nil {
			t.Fatalf("TranslateGlob(%q) = %q: %v", c.glob, TranslateGlob(c.glob), err)
		}
		if got := re.Match(c.subject); got != c.want {
			t.Errorf("glob %q on %q = %v, want %v (%s)", c.glob, c.subject, got, c.want, TranslateGlob(c.glob))
		}
	}
}

// 繰り返しを含む式を数 MB の文字列に当てても落ちず、打ち切りを知らせる（打ち切った照合を当たらなかったと読まない）。
func TestDepthLimit(t *testing.T) {
	long := strings.Repeat("ab", 2<<20)
	for _, p := range []string{`(?:ab)*c`, `(?:(a)(b))*?c`, `^(?!(?:ab)*$)`, `(?:(?=a)ab)*$`} {
		ok, err := MustCompile(p, IgnoreCase).SearchChecked(long)
		if ok || !errors.Is(err, ErrDepthLimit) {
			t.Errorf("%s: (%v, %v), want (false, ErrDepthLimit)", p, ok, err)
		}
		if MustCompile(p, IgnoreCase).Search(long) {
			t.Errorf("%s: Search の打ち切りは偽", p)
		}
	}
	// 上限に届かない長さでは答えが変わらない。
	short := strings.Repeat("ab", 4096)
	if ok, err := MustCompile(`(?:ab)*$`, 0).SearchChecked(short); !ok || err != nil {
		t.Errorf("short: (%v, %v)", ok, err)
	}
	if ok, err := MustCompile(`^(?!(?:ab)*$)`, 0).SearchChecked(short); ok || err != nil {
		t.Errorf("short lookahead: (%v, %v)", ok, err)
	}
}
