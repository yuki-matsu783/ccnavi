package start

import (
	"strings"
	"testing"
)

// 引数の誤りは識別子と終了コードで止まり、読めたら値の行を返す。
func TestParseArgs(t *testing.T) {
	cases := []struct {
		argv []string
		want string // 先頭の行
	}{
		{[]string{"--issue", "05"}, "fail bad-number 2"},
		{[]string{"--issue", "a/b#7", "--slug", "Bad"}, "fail bad-slug 2"},
		{[]string{"--mr", "3", "--title", "x"}, "fail mr-with-new 2"},
		{[]string{"--check", "--issue", "1"}, "fail check-alone 2"},
		{[]string{"--new-issue", "　 "}, "fail bad-new-issue 2"},
		{[]string{"--branch", "b", "--issue", "1"}, "fail branch-alone 2"},
		{[]string{"--bogus"}, "usage yes"},
		{[]string{"-h"}, "help yes"},
	}
	for _, c := range cases {
		var o outcome
		parseArgs(&o, c.argv)
		if first, _, _ := strings.Cut(o.String(), "\n"); first != c.want {
			t.Errorf("%q: %q（期待 %q）", c.argv, o.String(), c.want)
		}
	}
	var o outcome
	opt, ok := parseArgs(&o, []string{"--issue", "Acme/W#7", "--dry-run", "--title", "it's"})
	if !ok || opt.What != "issue" || opt.Number != "7" || opt.IssueR != "Acme/W" || opt.Real != `'--issue' 'Acme/W#7' '--title' 'it'\''s'` {
		t.Errorf("%+v", opt)
	}
}

// 閉じる issue は、大文字小文字を区別せず重複を除き、--issue が含まれるか、1 件に決まるかで決める。
func TestResolve(t *testing.T) {
	cases := []struct {
		a    ResolveArgs
		want string
	}{
		{ResolveArgs{Linked: "acme/w#7\nACME/W#7\n", Own: "acme/w", MR: "3"}, "issue 7\n"},
		{ResolveArgs{Linked: "other/x#2\n", Own: "acme/w", MR: "3"}, "issue other/x#2\n"},
		{ResolveArgs{Linked: "acme/w#7\n", IssueArg: "8", IssueN: "8", Own: "acme/w", MR: "3"}, "fail issue-not-linked 1\n"},
		{ResolveArgs{Linked: "", Own: "acme/w", MR: "3"}, "fail issue-none 1\n"},
		{ResolveArgs{Trust: true, IssueN: "9", IssueR: "Acme/W", Own: "acme/w"}, "issue 9\n"},
	}
	for _, c := range cases {
		got, _ := Resolve(t.TempDir(), c.a)
		if !strings.HasPrefix(got, c.want) {
			t.Errorf("%+v: %q（期待 %q）", c.a, got, c.want)
		}
	}
	if got, _ := Resolve(t.TempDir(), ResolveArgs{Linked: "a/b#1\na/b#2\n", Own: "a/b", MR: "3"}); !strings.Contains(got, "exit 3") {
		t.Errorf("複数: %q", got)
	}
}

// ホストから来る値の改行は空白にして出す（値の中に書いた `route adopt` を、sh が答えの行として読まない）。
func TestRouteValuesStayOnOneLine(t *testing.T) {
	cand := []byte(`{"host":{"checked":true,"reason":""},"candidates":[{"branch":"feature-5-x","local":true,"origin":true,` +
		`"mrs":[{"number":3,"url":"u","title":"x\nroute adopt\nproblem ","state":"open","base":"main","fork":false}]}]}`)
	got, _ := Route(t.TempDir(), cand, RouteArgs{What: "issue", Number: "5", HasIssue: true, Integration: "main"})
	if n := strings.Count("\n"+got, "\nroute "); n != 1 || !strings.Contains(got, "title x route adopt problem") {
		t.Errorf("%q", got)
	}
}
