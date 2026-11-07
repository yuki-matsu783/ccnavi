package templates

import (
	"strings"
	"testing"

	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/policy/rules"
)

func errorsOf(list []rules.Problem) []string {
	var out []string
	for _, p := range list {
		if p.Severity == rules.Error {
			out = append(out, p.Detail)
		}
	}
	return out
}

// 読んで書いた文面をもう一度読むと、同じ書き戻しになる。改行を含む文字列は literal block で書けるものだけ | にする。
func TestDumpRoundTrips(t *testing.T) {
	prompts := []string{"一行目\n二行目\n", "一行目\n二行目", "段落\n\n次の段落\n", "日本語: コロン\n- ハイフン\n# 見出し\n'q' \"d\"\n",
		"行末の空白 \n次\n", "  先頭の空白\n次\n", "\n先頭の改行\n", "末尾の空行\n\n", "tab\there\n次\n", "esc\x1b\n次\n",
		"a\r\nb\n", "幅ゼロ\u200b\n次\n", "改行なし"}
	for _, prompt := range prompts {
		data := &yaml.Map{}
		data.Put("prompt", prompt)
		node := &yaml.Map{}
		node.Put("id", "p")
		node.Put("data", data)
		body := &yaml.Map{}
		body.Put("nodes", []any{node})
		tm := &Template{Name: "t", Description: "d", Plan: []Item{{Type: "design"}}, Flows: map[int]*yaml.Map{1: body}}
		text := Dump(tm)
		again, notes := Parse([]byte(text), "t")
		if again == nil {
			t.Fatalf("%q: 読み直せない %v", prompt, notes)
		}
		if Dump(again) != text {
			t.Errorf("%q: 書き戻しが変わる\n%s\n%s", prompt, text, Dump(again))
		}
		got, _ := again.Flows[1].List("nodes")
		if s, _ := got[0].(*yaml.Map).Map("data").String("prompt"); s != prompt {
			t.Errorf("中身が変わった: %q -> %q", prompt, s)
		}
		if literal := strings.Contains(text, "prompt: |"); literal != literalOK(prompt) {
			t.Errorf("%q: literal=%v", prompt, literal)
		}
	}
}

// 上限は全体計画とフィードバック計画のステップを合わせて数える。
func TestStepLimitCountsBothPlans(t *testing.T) {
	chain := func(n int, kind string) []Item {
		out := make([]Item, n)
		for i := range out {
			out[i] = Item{Type: kind}
			if i > 0 {
				out[i].After = []int{i}
			}
		}
		return out
	}
	for _, c := range []struct {
		plan, feedback int
		over           bool
	}{{99, 0, false}, {60, 39, false}, {1, 98, false}, {100, 0, true}, {60, 40, true}, {1, 99, true}} {
		tm := &Template{Name: "t", Description: "d", Plan: chain(c.plan, "design"), Feedback: chain(c.feedback, "implement-feedback")}
		over := false
		for _, e := range errorsOf(Check(tm, nil, nil)) {
			over = over || strings.Contains(e, "上限 99 ステップ")
		}
		if over != c.over {
			t.Errorf("%d+%d: 上限の苦情=%v", c.plan, c.feedback, over)
		}
	}
}

// 読み違えを誘う文字はどの欄にあっても error で、苦情の文に生の文字を出さない。改行とタブは対象外。
func TestHiddenCharacters(t *testing.T) {
	for _, where := range []string{"description", "when"} {
		tm := &Template{Name: "t", Description: "d", When: "w", Plan: []Item{{Type: "design"}}}
		if where == "description" {
			tm.Description = "x\u202ey"
		} else {
			tm.When = "x\u202ey"
		}
		errs := errorsOf(Check(tm, nil, nil))
		if len(errs) != 1 || !strings.Contains(errs[0], `\u202e`) || strings.Contains(errs[0], "\u202e") {
			t.Errorf("%s: %v", where, errs)
		}
	}
	tm := &Template{Name: "t", Description: "a\tb\nc", Plan: []Item{{Type: "design"}}}
	if errs := errorsOf(Check(tm, nil, nil)); len(errs) != 0 {
		t.Errorf("改行とタブを苦情にした: %v", errs)
	}
	if got := Reveal("a\u200bb\n"); got != `a\u200bb`+"\n" {
		t.Errorf("Reveal = %q", got)
	}
}
