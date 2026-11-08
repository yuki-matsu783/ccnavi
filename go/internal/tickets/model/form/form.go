package form

import (
	"maps"
	"slices"
	"strings"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/model/ticket"
)

// parentHeadings は親の必須の見出し（雛形の本文の並び）。
var parentHeadings = []string{"目的", "範囲", "対象外", "完了の確かめ方", "計画の理由"}

// ParentHeadings は parentHeadings の写しを返す（呼び手が書き換えても元は変わらない）。
func ParentHeadings() []string { return slices.Clone(parentHeadings) }

// RationaleTodo は rationale の空欄の目印。
const RationaleTodo = "（ここにこの作業をする理由を書く）"

// headingTodo は見出しごとの空欄の目印。雛形は見出しの下に 1 行ずつ置く。
var headingTodo = map[string]string{
	"目的":      "（ここにこの作業の目的を書く）",
	"範囲":      "（ここにこの作業の範囲を書く）",
	"対象外":     "（ここにこの作業の対象外を書く）",
	"完了の確かめ方": "（ここに完了をどう確かめるかを書く）",
	"計画の理由":   "（ここに計画の理由を書く）",
}

// HeadingTodo は headingTodo の写しを返す（呼び手が書き換えても元は変わらない）。
func HeadingTodo() map[string]string { return maps.Clone(headingTodo) }

// placeholders は予約した目印。提案に 1 つでも残っていれば空欄が埋まっていない。並びは rationale・見出しの順。
var placeholders = func() []string {
	out := []string{RationaleTodo}
	for _, name := range parentHeadings {
		out = append(out, headingTodo[name])
	}
	return out
}()

const (
	fenceChars = "`~"
	// fenceIndent より深い字下げの行はコードブロックで、フェンスではない（Markdown の決まり）。
	fenceIndent = 3
)

type fence struct {
	char rune
	run  int
	rest string
}

func fenceOf(line string) (fence, bool) {
	stripped := strings.TrimLeft(line, " ")
	if len(line)-len(stripped) > fenceIndent {
		return fence{}, false
	}
	for _, ch := range fenceChars {
		rest := strings.TrimLeft(stripped, string(ch))
		if run := len(stripped) - len(rest); run >= 3 {
			return fence{ch, run, rest}, true
		}
	}
	return fence{}, false
}

// headings は本文のコードフェンスの外にある `## <名前>` の名前の集まり。フェンスは開きと同じ記号で、開き以上の
// 長さの、記号だけの行で閉じる。閉じなければ末尾まで続く。
func headings(body string) map[string]bool {
	found := map[string]bool{}
	var open *fence
	for _, line := range ospath.Splitlines(body) {
		f, isFence := fenceOf(line)
		if open == nil {
			if isFence {
				open = &f
				continue
			}
		} else {
			if isFence && f.char == open.char && f.run >= open.run && ospath.Strip(f.rest) == "" {
				open = nil
			}
			continue
		}
		if name, ok := strings.CutPrefix(line, "## "); ok {
			found[ospath.Strip(name)] = true
		}
	}
	return found
}

// Problems は提案の形式の苦情。目印の残りは新規の提案なら error、改版（revision）なら warn（承認済みチケットの
// コピーなので、残りで止めない）。見出しの欠けは親の新規の提案だけを warn で言う。
func Problems(t *ticket.Ticket, revision bool) []rules.Problem {
	var out []rules.Problem
	severity := rules.Error
	if revision {
		severity = rules.Warn
	}
	for _, part := range []struct{ place, text string }{{"rationale", t.Rationale}, {"本文", t.Body}} {
		var left []string
		for _, mark := range placeholders {
			if strings.Contains(part.text, mark) {
				left = append(left, "「"+mark+"」")
			}
		}
		if len(left) == 0 {
			continue
		}
		out = append(out, rules.Problem{Severity: severity, Rule: t.ID,
			Detail: part.place + "に雛形の空欄の目印が残っている: " + strings.Join(left, "、") + "。" +
				"実際の内容に書き換えてから承認を頼みます"})
	}
	if revision || t.IsChild() {
		return out
	}
	present := headings(t.Body)
	var missing []string
	for _, name := range parentHeadings {
		if !present[name] {
			missing = append(missing, "`## "+name+"`")
		}
	}
	if len(missing) > 0 {
		out = append(out, rules.Problem{Severity: rules.Warn, Rule: t.ID,
			Detail: "親の提案の本文に見出しが欠けている: " + strings.Join(missing, "、") + "。" +
				"Stop の判定は親の提案に目的・範囲・対象外・完了の確かめ方を求める"})
	}
	return out
}
