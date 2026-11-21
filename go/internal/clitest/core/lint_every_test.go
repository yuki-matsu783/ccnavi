package core

import (
	"strings"
	"testing"
)

// `--lint` が `every`（渡す回の刻み）と、message の中の `.ccnavi/scripts/` について言うこと。
// 渡す回の刻みそのものは go/internal/config の見本が見る。

const note = "ルールと突き合わせること。"

// narrow は狭い allow。広い allow の warn（既存の検査）に引っかからない形で書く。
func narrow(name string, extra ...any) rule {
	return with(rule{"id": name, "match": "Write", "glob": "*/src/*"}, extra...)
}

func denyRule(name, message string) rule {
	return rule{"id": name, "match": "Bash", "glob": "*git push*", "message": message}
}

// lintAllow は push を止める deny 1 件と、渡した allow を rules.yml に書いて --lint する。
func lintAllow(t *testing.T, root string, allow ...rule) (code int, stdout string) {
	t.Helper()
	body := map[string]any{"version": 1, "deny": []rule{denyRule("push", "push はユーザが行う")}, "allow": allow}
	r := ccnavi(t, root, nil, "--rules", writeFile(t, root, "rules.yml", mustJSON(t, body)), "--lint")
	return r.Code, r.Stdout
}

// said はこのルールについて lint が言った行。何も言われていなければ空。
func said(stdout, name string) []string {
	var lines []string
	for _, line := range strings.Split(stdout, "\n") {
		if strings.Contains(line, ": "+name+": ") {
			lines = append(lines, line)
		}
	}
	return lines
}

func anyLine(lines []string, prefix string, contains ...string) bool {
	for _, l := range lines {
		ok := strings.HasPrefix(l, prefix)
		for _, c := range contains {
			ok = ok && strings.Contains(l, c)
		}
		if ok {
			return true
		}
	}
	return false
}

// every の値が 1 以上の整数か。刻みとして読めない値を何も言わずに無視すると、書いたユーザは刻んだつもりのまま毎回渡ることになる。
// `every: 1` は every 無しと同じ意味になるだけで誤りではない。
func TestEveryMustBeAPositiveInteger(t *testing.T) {
	t.Parallel()
	root := emptyRoot(t)
	code, out := lintAllow(t, root,
		narrow("zero", "additionalContext", note, "every", 0),
		narrow("minus", "additionalContext", note, "every", -1),
		narrow("word", "additionalContext", note, "every", "x"),
		narrow("just-one", "additionalContext", note, "every", 1),
		narrow("five", "additionalContext", note, "every", 5))
	if code == 0 {
		t.Errorf("error があるのに 0 で終わった: %s", out)
	}
	for _, name := range []string{"zero", "minus", "word"} {
		if !anyLine(said(out, name), "error:", "every") {
			t.Errorf("%s に every の error が無い:\n%s", name, out)
		}
	}
	for _, name := range []string{"just-one", "five"} {
		if got := said(out, name); len(got) != 0 {
			t.Errorf("%s は何も言われない: %v", name, got)
		}
	}
}

// 渡すものを 1 つも持たない every は warn。刻んでも渡す文が無ければ何も起きない。
// additionalContextOnce だけ・additionalContextFile だけの every は渡すものがあるので報告しない。
func TestEveryWithNothingToDeliverIsAWarn(t *testing.T) {
	t.Parallel()
	root := emptyRoot(t)
	writeFile(t, root, "docs/guide.md", "突き合わせの観点")
	code, out := lintAllow(t, root,
		narrow("silent", "every", 5),
		narrow("only-once", "additionalContextOnce", note, "every", 5),
		narrow("only-file", "additionalContextFile", "docs/guide.md", "every", 5))
	if code != 0 {
		t.Errorf("code = %d: %s", code, out)
	}
	if !anyLine(said(out, "silent"), "warn:", "every") {
		t.Errorf("文を持たない every の warn が無い:\n%s", out)
	}
	for _, name := range []string{"only-once", "only-file"} { // 渡す回の最初の 1 回として有効・本文は渡る
		if got := said(out, name); len(got) != 0 {
			t.Errorf("%s は報告しない: %v", name, got)
		}
	}
}

// message の `.ccnavi/scripts/` に `{root}` が付いていなければ warn（issue #211）。
func TestBareScriptsPathInMessageIsAWarn(t *testing.T) {
	t.Parallel()
	root := emptyRoot(t)
	body := map[string]any{"version": 1, "deny": []rule{
		denyRule("bare", "'sh .ccnavi/scripts/ccnavi-git.sh ...' を使ってください"),
		denyRule("rooted", "'sh {root}/.ccnavi/scripts/ccnavi-git.sh ...' を使う"),
		denyRule("plain", "push はユーザが行う"),
	}}
	r := ccnavi(t, root, nil, "--rules", writeFile(t, root, "rules.yml", mustJSON(t, body)), "--lint")
	if r.Code != 0 {
		t.Errorf("code = %d: %s", r.Code, r.Stdout)
	}
	if !anyLine(said(r.Stdout, "bare"), "warn:", "{root}") {
		t.Errorf("bare に warn が無い: %s", r.Stdout)
	}
	for _, name := range []string{"rooted", "plain"} {
		if got := said(r.Stdout, name); len(got) != 0 {
			t.Errorf("%s は言われない: %v", name, got)
		}
	}
}

// 他の文面の欄（additionalContext・additionalContextOnce）も調べ、欄の名前を言う。
func TestOtherTextFieldsAreCheckedAndNamed(t *testing.T) {
	t.Parallel()
	root := emptyRoot(t)
	sh := "sh .ccnavi/scripts/x.sh"
	_, out := lintAllow(t, root, narrow("ctx", "additionalContext", sh), narrow("once", "additionalContextOnce", sh))
	if !anyLine(said(out, "ctx"), "warn:", "additionalContext の") || !anyLine(said(out, "once"), "warn:", "additionalContextOnce の") {
		t.Errorf("欄の名前つきの warn が無い:\n%s", out)
	}
}

// 絶対パスは warn しない。相対パス（. や .. 始まり・括弧やリンクの中・= の後ろを含む）は warn する。
func TestScriptsPathWarnsOnlyWhenRelative(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name string
		warn bool
		text string
	}{
		{"slash", false, "sh /opt/ws/.ccnavi/scripts/x.sh"},
		{"home", false, "sh ~/ws/.ccnavi/scripts/x.sh"},
		{"quoted", false, "sh '/opt/myws/.ccnavi/scripts/x.sh'"},
		{"drive-back", false, `sh C:\ws/.ccnavi/scripts/x.sh`},
		{"drive-fwd", false, `sh "d:/ws/.ccnavi/scripts/x.sh"`},
		{"rooted", false, "sh {root}/.ccnavi/scripts/x.sh"},
		{"space", false, "sh '/c/Users/John Smith/ws/.ccnavi/scripts/x.sh'"},
		{"fullwidth", false, "（/opt/ws/.ccnavi/scripts/x.sh を使う）"},
		{"corner", false, "「/opt/ws/.ccnavi/scripts/x.sh」を使う"},
		{"link", false, "[x](/opt/ws/.ccnavi/scripts/x.sh)"},
		{"equals", false, "sh --script=/opt/ws/.ccnavi/scripts/x.sh"},
		{"var", false, "sh $CLAUDE_PROJECT_DIR/.ccnavi/scripts/x.sh"},
		{"braced-var", false, "sh ${CLAUDE_PROJECT_DIR}/.ccnavi/scripts/x.sh"},
		{"rel-bare", true, "sh .ccnavi/scripts/x.sh"},
		{"rel-dot", true, "sh ./.ccnavi/scripts/x.sh"},
		{"rel-up", true, "sh ../../.ccnavi/scripts/x.sh"},
		{"rel-paren", true, "（.ccnavi/scripts/x.sh を使う）"},
		{"rel-corner", true, "「./.ccnavi/scripts/x.sh」を使う"},
		{"rel-equals", true, "sh --script=.ccnavi/scripts/x.sh"},
		{"rel-link", true, "[x](../.ccnavi/scripts/x.sh)"},
		{"rel-mixed", true, "sh /abs/.ccnavi/scripts/a.sh と sh .ccnavi/scripts/b.sh"},
	}
	root := emptyRoot(t)
	var allow []rule
	for _, c := range cases {
		allow = append(allow, narrow(c.name, "additionalContext", c.text))
	}
	code, out := lintAllow(t, root, allow...)
	if code != 0 {
		t.Errorf("code = %d: %s", code, out)
	}
	for _, c := range cases {
		if got := anyLine(said(out, c.name), "warn:"); got != c.warn {
			t.Errorf("%s (%q): warn = %v, want %v\n%s", c.name, c.text, got, c.warn, out)
		}
	}
}
