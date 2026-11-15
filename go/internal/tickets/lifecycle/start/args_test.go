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
		{nil, "usage yes"},
		{[]string{"--issue"}, "fail no-number 2"},
		{[]string{"--issue", "0"}, "fail bad-number 2"},
		{[]string{"--issue", "1", "--mr", "x"}, "fail bad-number 2"},
		{[]string{"--issue", "a/b#c"}, "fail bad-number 2"},
		{[]string{"--issue", "1", "--slug", "Bad Slug"}, "fail bad-slug 2"},
		{[]string{"--issue", "152", "--slug", "<語>"}, "fail bad-slug 2"},
		{[]string{"--branch", "x", "--mr", "1"}, "fail branch-alone 2"},
		{[]string{"--check", "--dry-run"}, "fail check-alone 2"},
		{[]string{"--check", "--mr", "1"}, "fail check-alone 2"},
		{[]string{"--list-open", "--dry-run"}, "fail list-open-alone 2"},
		{[]string{"--mr", "1", "--slug", "x"}, "fail mr-with-new 2"},
		{[]string{"--mr", "5", "--reopened"}, "fail reopened-alone 2"},
		{[]string{"--new-issue", "x", "--issue", "1"}, "fail new-issue-alone 2"},
		{[]string{"--new-issue", "x", "--mr", "1"}, "fail new-issue-alone 2"},
		{[]string{"--new-issue", "x", "--mcp-checked"}, "fail new-issue-alone 2"},
		{[]string{"--new-issue", "   "}, "fail bad-new-issue 2"},
		{[]string{"--new-issue", strings.Repeat("x", 201)}, "fail bad-new-issue 2"},
		{[]string{"--issue-body", "b", "--issue", "1"}, "fail body-alone 2"},
		{[]string{"--similar-checked", "--issue", "1"}, "fail similar-alone 2"},
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
	// --new-issue のタイトルは字で数える（日本語 100 字は通る）
	if opt, ok := parseArgs(&o, []string{"--new-issue", strings.Repeat("あ", 100), "--similar-checked"}); !ok || opt.NewTitle != strings.Repeat("あ", 100) {
		t.Errorf("日本語 100 字: %+v", opt)
	}
	// 引数の順によらず、--mr があれば MR で始める
	if opt, ok := parseArgs(&o, []string{"--issue", "13", "--mr", "5"}); !ok || opt.What != "mr" || opt.Number != "5" || opt.IssueN != "13" {
		t.Errorf("--issue と --mr: %+v", opt)
	}
	// 打ち直しの引数は、シェルの特殊な字を含んでも 1 引数ずつ単引用符で囲む
	if opt, _ := parseArgs(&o, []string{"--issue", "152", "--title", `It's $HOME  "x" ` + "`a`"}); opt.Real != `'--issue' '152' '--title' 'It'\''s $HOME  "x" `+"`a`'" {
		t.Errorf("引用: %s", opt.Real)
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
		{ResolveArgs{Linked: "acme/w#12\nacme/w#13\n", IssueArg: "13", IssueN: "13", Own: "acme/w", MR: "5"}, "issue 13\n"},
		{ResolveArgs{Linked: "acme/w#12\n", IssueArg: "ACME/W#12", IssueN: "12", IssueR: "ACME/W", Own: "acme/w", MR: "5"}, "issue 12\n"},
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

// 止める文は表から引き、値を差し込む。知らない名前は偽。
func TestStop(t *testing.T) {
	got, known := Stop(t.TempDir(), "host-unreadable", map[string]string{"host_why": "origin が無い"})
	if !known || got != "fail host-unreadable 1\nmsg MR を作れません（origin が無い）。origin を確かめてください。\n" {
		t.Errorf("%q", got)
	}
	if got, _ := Stop(t.TempDir(), "multi", map[string]string{"total": "2"}); !strings.Contains(got, "exit 3") || !strings.Contains(got, "reason multi") {
		t.Errorf("%q", got)
	}
	if _, known := Stop(t.TempDir(), "nope", nil); known {
		t.Error("知らない名前")
	}
}

// 値の中の `{名前}` は、もう一度差し込まない（並び順で結果が変わらない）。
func TestStopDoesNotExpandValues(t *testing.T) {
	for range 20 {
		got, _ := Stop(t.TempDir(), "issue-missing-resume", map[string]string{"number": "5", "issue_why": "bad {number} here"})
		if !strings.Contains(got, "（bad {number} here）") {
			t.Fatalf("%q", got)
		}
	}
}
