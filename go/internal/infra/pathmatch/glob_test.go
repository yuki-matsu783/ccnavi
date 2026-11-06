package pathmatch

import (
	"regexp"
	"testing"
)

// 呼び手（ルールの読み込み）と同じく、先頭に ^ を付けて当てる。
func hit(t *testing.T, glob, subject string) bool {
	t.Helper()
	re, err := regexp.Compile("^" + Translate(glob))
	if err != nil {
		t.Fatalf("Translate(%q) = %q: %v", glob, Translate(glob), err)
	}
	return re.MatchString(subject)
}

func TestTranslate(t *testing.T) {
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
		{"*sed *", "cd /repo && sed -i s/a/b/ x", true},
		{"*git push*", "legit push", true},
		{"*foo*bar*", "foobar", true},
		{"*id_?sa*", "id_rsa", true},
		// いま使っている書き方。
		{"*rm -rf *", "rm -r /tmp/x", false},
		{"*/.ccnavi/*", "/repo/.ccnavi/config/common/rules.yml", true},
		{"*/.ccnavi/*", "/repo/.claude/hooks/lint.sh", false},
		{"*.env*", "/repo/.env.local", true},
		{"*", "なんでも", true},
		{"*", "a\nb", true},
		{"a?c", "a\nc", true},
		{"?", "é", true},
		{"a.b", "axb", false},
		// 文字クラスは fnmatch と同じ答え（Python の答えを写した）。
		{"[a-c]x", "bx", true},
		{"[!a-c]x", "ax", false},
		{"[!a-c]x", "dx", true},
		{"[]]", "]", true},
		{"[!]]", "a", true},
		{"[z-a]", "m", false},
		{"[a-]", "-", true},
		{"[]-a]", "-", false},
		{"[]-a]", "^", true},
		{`[\]`, `\`, true},
		{"[!]", "[!]", true},
		{"a[b", "a[b", true},
		{"[^a]", "^", true},
		{"[^a]", "b", false},
		{"[&]", "&", true},
		{"[[:x]", ":", true},
		// クラスの中の "/" も置き換わり、クラスが途中で閉じる（Python と同じ）。
		{"[a/b]", "/", false},
		{"[a/b]", `\b]`, true},
	}
	for _, c := range cases {
		if got := hit(t, c.glob, c.subject); got != c.want {
			t.Errorf("glob %q on %q = %v, want %v (%s)", c.glob, c.subject, got, c.want, Translate(c.glob))
		}
	}
}
