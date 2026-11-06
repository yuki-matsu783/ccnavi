package form

import (
	"strings"
	"testing"

	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/model/ticket"
)

func without(names ...string) string {
	var b strings.Builder
	for _, name := range ParentHeadings {
		skip := false
		for _, n := range names {
			skip = skip || n == name
		}
		if !skip {
			b.WriteString("## " + name + "\n\n書いた内容\n\n")
		}
	}
	return b.String()
}

var filled = without()

func proposal(body, rationale string, child bool) *ticket.Ticket {
	t := &ticket.Ticket{ID: "feature-1-demo", Rationale: rationale, Body: body}
	if child {
		t.ID, t.Parent, t.Phase = "feature-1-demo-01-01", "feature-1-demo", 1
	}
	return t
}

func severities(ps []rules.Problem) string {
	var out []string
	for _, p := range ps {
		out = append(out, string(p.Severity))
	}
	return strings.Join(out, ",")
}

func TestProblems(t *testing.T) {
	mark := HeadingTodo["範囲"]
	cases := []struct {
		name     string
		t        *ticket.Ticket
		revision bool
		want     string   // 深刻度の並び
		named    []string // 最初の苦情に出る語
	}{
		{"rationale の目印は新規なら error", proposal(filled, RationaleTodo, false), false, "error", []string{"rationale", RationaleTodo}},
		{"本文の目印は名指しする", proposal(strings.Replace(filled, "書いた内容", mark, 1), "理由", false), false, "error", []string{"本文", mark}},
		{"改版なら warn", proposal(filled+Placeholders[1], RationaleTodo, false), true, "warn,warn", nil},
		{"改版でなければ error", proposal(filled+Placeholders[1], RationaleTodo, false), false, "error,error", nil},
		{"見出しの欠けは 1 件にまとめる", proposal(without("範囲", "対象外"), "理由", false), false, "warn", []string{"## 範囲", "## 対象外"}},
		{"``` の中の見出しは数えない", proposal(without("目的")+"```\n## 目的\n```\n", "理由", false), false, "warn", []string{"## 目的"}},
		{"~~~ の中の見出しは数えない", proposal(without("目的")+"~~~\n## 目的\n~~~\n", "理由", false), false, "warn", []string{"## 目的"}},
		{"閉じたフェンスの後ろは数える", proposal("```\ncode\n```\n"+filled, "理由", false), false, "", nil},
		{"短いフェンスでは閉じない", proposal(without("目的")+"````md\n```\n## 目的\n```\n````\n", "理由", false), false, "warn", nil},
		{"記号だけの行でだけ閉じる", proposal(without("目的")+"```py\n## 目的\n```not close\n## 目的\n```\n", "理由", false), false, "warn", nil},
		{"閉じないフェンスは末尾まで", proposal("```\n"+filled, "理由", false), false, "warn", ParentHeadings},
		{"4 字の字下げはフェンスでない", proposal("    ```\n"+filled, "理由", false), false, "", nil},
		{"CRLF を読む", proposal(strings.ReplaceAll(filled, "\n", "\r\n"), "理由", false), false, "", nil},
		{"深さの違う見出しは数えない", proposal(without("目的")+"### 目的\n\n書いた内容\n", "理由", false), false, "warn", []string{"## 目的"}},
		{"子は見出しを言わない", proposal("", "理由", true), false, "", nil},
		{"子でも目印は言う", proposal("", RationaleTodo, true), false, "error", nil},
		{"改版は見出しを言わない", proposal("", "理由", false), true, "", nil},
		{"そろった親は何も言わない", proposal(filled, "理由", false), false, "", nil},
		{"見出しの名前は前後の空白を除く", proposal(strings.Replace(filled, "## 目的", "## 目的  ", 1), "理由", false), false, "", nil},
	}
	for _, c := range cases {
		found := Problems(c.t, c.revision)
		if got := severities(found); got != c.want {
			t.Errorf("%s: %q, want %q (%v)", c.name, got, c.want, found)
			continue
		}
		for _, word := range c.named {
			if !strings.Contains(found[0].Detail, word) {
				t.Errorf("%s: %q が無い: %s", c.name, word, found[0].Detail)
			}
		}
	}
}
