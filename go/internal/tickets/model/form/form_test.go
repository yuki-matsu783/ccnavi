package form

import (
	"strings"
	"testing"

	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/model/ticket"
)

// testSections はこのリポジトリの ticket-sections.yml と同じ見出し。
var testSections = Sections{
	Parent: []Heading{
		{"目的", "（ここにこの作業の目的を書く）"},
		{"範囲", "（ここにこの作業の範囲を書く）"},
		{"対象外", "（ここにこの作業の対象外を書く）"},
		{"完了の確かめ方", "（ここに完了をどう確かめるかを書く）"},
		{"計画の理由", "（ここに計画の理由を書く）"},
	},
	Child: []Heading{{Name: "担当範囲"}, {Name: "親の要求との対応"}, {Name: "確認の手順"}},
}

var parentHeadings = Names(testSections.Parent)

func without(names ...string) string {
	var b strings.Builder
	for _, name := range parentHeadings {
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

var childFilled = func() string {
	var b strings.Builder
	for _, name := range Names(testSections.Child) {
		b.WriteString("## " + name + "\n\n書いた内容\n\n")
	}
	return b.String()
}()

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
	mark := testSections.Parent[1].Todo
	marks := testSections.placeholders()
	cases := []struct {
		name     string
		t        *ticket.Ticket
		revision bool
		want     string   // 深刻度の並び
		named    []string // 最初の苦情に出る語
	}{
		{"rationale の目印は新規なら error", proposal(filled, RationaleTodo, false), false, "error", []string{"rationale", RationaleTodo}},
		{"本文の目印は名指しする", proposal(strings.Replace(filled, "書いた内容", mark, 1), "理由", false), false, "error", []string{"本文", mark}},
		{"改版なら warn", proposal(filled+marks[1], RationaleTodo, false), true, "warn,warn", nil},
		{"改版でなければ error", proposal(filled+marks[1], RationaleTodo, false), false, "error,error", nil},
		{"見出しの欠けは 1 件にまとめる", proposal(without("範囲", "対象外"), "理由", false), false, "warn", []string{"## 範囲", "## 対象外"}},
		{"``` の中の見出しは数えない", proposal(without("目的")+"```\n## 目的\n```\n", "理由", false), false, "warn", []string{"## 目的"}},
		{"~~~ の中の見出しは数えない", proposal(without("目的")+"~~~\n## 目的\n~~~\n", "理由", false), false, "warn", []string{"## 目的"}},
		{"閉じたフェンスの後ろは数える", proposal("```\ncode\n```\n"+filled, "理由", false), false, "", nil},
		{"短いフェンスでは閉じない", proposal(without("目的")+"````md\n```\n## 目的\n```\n````\n", "理由", false), false, "warn", nil},
		{"記号だけの行でだけ閉じる", proposal(without("目的")+"```py\n## 目的\n```not close\n## 目的\n```\n", "理由", false), false, "warn", nil},
		{"閉じないフェンスは末尾まで", proposal("```\n"+filled, "理由", false), false, "warn", parentHeadings},
		{"4 字の字下げはフェンスでない", proposal("    ```\n"+filled, "理由", false), false, "", nil},
		{"CRLF を読む", proposal(strings.ReplaceAll(filled, "\n", "\r\n"), "理由", false), false, "", nil},
		{"深さの違う見出しは数えない", proposal(without("目的")+"### 目的\n\n書いた内容\n", "理由", false), false, "warn", []string{"## 目的"}},
		{"子の見出しの欠けは 1 件にまとめる", proposal("## 担当範囲\n\n書いた内容\n", "理由", true), false, "warn", []string{"子の提案", "## 親の要求との対応", "## 確認の手順"}},
		{"子に親の見出しは求めない", proposal(childFilled, "理由", true), false, "", nil},
		{"子でも目印は言う", proposal(childFilled, RationaleTodo, true), false, "error", nil},
		{"改版は見出しを言わない", proposal("", "理由", false), true, "", nil},
		{"そろった親は何も言わない", proposal(filled, "理由", false), false, "", nil},
		{"見出しの名前は前後の空白を除く", proposal(strings.Replace(filled, "## 目的", "## 目的  ", 1), "理由", false), false, "", nil},
		{"苦情はファイルと種類を名指しする", proposal(without("目的"), "理由", false), false, "warn", []string{"ticket-sections.yml の parent"}},
		{"子の苦情は child を名指しする", proposal("", "理由", true), false, "warn", []string{"ticket-sections.yml の child"}},
	}
	for _, c := range cases {
		found := Problems(c.t, c.revision, testSections)
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

func TestProblemsFollowSections(t *testing.T) {
	custom := Sections{Parent: []Heading{{Name: "Goal", Todo: "(goal)"}}}
	// 見出しは大文字小文字を区別せずに探す。
	if found := Problems(proposal("## goal\n\n書いた\n", "理由", false), false, custom); len(found) != 0 {
		t.Errorf("大文字小文字の違いで欠けとした: %v", found)
	}
	// 渡した見出しの目印だけを目印として見る。
	if found := Problems(proposal("## Goal\n\n(goal)\n", "理由", false), false, custom); severities(found) != "error" {
		t.Errorf("渡した目印を見ない: %v", found)
	}
	if found := Problems(proposal("## Goal\n\n"+testSections.Parent[0].Todo+"\n", "理由", false), false, custom); len(found) != 0 {
		t.Errorf("渡していない目印を見た: %v", found)
	}
	// 見出しが空なら、親も子も見出しを言わない。
	for _, child := range []bool{false, true} {
		if found := Problems(proposal("", "理由", child), false, Sections{}); len(found) != 0 {
			t.Errorf("見出しが空なのに言った（子 %v）: %v", child, found)
		}
	}
}
