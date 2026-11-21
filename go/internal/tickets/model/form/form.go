package form

import (
	"slices"
	"strings"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/model/ticket"
)

// SectionsFile は見出しの決まりのファイル名（苦情で名指しする）。
const SectionsFile = "ticket-sections.yml"

// Heading は提案の本文に求める `## ` の見出し 1 つ。Todo は空欄の目印（雛形が見出しの下に置く 1 行。空なら置かない）。
type Heading struct {
	Name string
	Todo string
}

// Sections は親と子の提案に求める見出し（ticket-sections.yml の parent と child）。空の並びはその種類の見出しを見ない。
type Sections struct {
	Parent []Heading
	Child  []Heading
}

// Names は見出しの名前の並び。
func Names(list []Heading) []string {
	out := make([]string, len(list))
	for i, h := range list {
		out[i] = h.Name
	}
	return out
}

// RationaleTodo は rationale の空欄の目印。
const RationaleTodo = "（ここにこの作業をする理由を書く）"

// placeholders は予約した目印。提案に 1 つでも残っていれば空欄が埋まっていない。並びは rationale・親・子の見出しの順。
func (s Sections) placeholders() []string {
	out := []string{RationaleTodo}
	for _, h := range slices.Concat(s.Parent, s.Child) {
		if h.Todo != "" && !slices.Contains(out, h.Todo) {
			out = append(out, h.Todo)
		}
	}
	return out
}

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

// headings は本文のコードフェンスの外にある `## <名前>` の名前の集まり。キーは大文字小文字を区別しない比べの形。
// フェンスは開きと同じ記号で、開き以上の長さの、記号だけの行で閉じる。閉じなければ末尾まで続く。
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
			found[pyfmt.Casefold(ospath.Strip(name))] = true
		}
	}
	return found
}

// Problems は提案の形式の苦情。目印（rationale の目印と sections の各見出しの目印）の残りは新規の提案なら error、
// 改版（revision）なら warn（承認済みチケットのコピーなので、残りで止めない）。見出しの欠けは新規の提案（親と子）
// だけを warn で言う。求める見出しは sections（その提案のレイヤーの ticket-sections.yml）。
func Problems(t *ticket.Ticket, revision bool, sections Sections) []rules.Problem {
	var out []rules.Problem
	severity := rules.Error
	if revision {
		severity = rules.Warn
	}
	marks := sections.placeholders()
	for _, part := range []struct{ place, text string }{{"rationale", t.Rationale}, {"本文", t.Body}} {
		var left []string
		for _, mark := range marks {
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
	if revision {
		return out
	}
	who, key, want := "親", "parent", sections.Parent
	if t.IsChild() {
		who, key, want = "子", "child", sections.Child
	}
	present := headings(t.Body)
	var missing []string
	for _, h := range want {
		if !present[pyfmt.Casefold(h.Name)] {
			missing = append(missing, "`## "+h.Name+"`")
		}
	}
	if len(missing) > 0 {
		out = append(out, rules.Problem{Severity: rules.Warn, Rule: t.ID,
			Detail: who + "の提案の本文に見出しが欠けている: " + strings.Join(missing, "、") + "。" +
				SectionsFile + " の " + key + " が求める見出しで、Stop の判定も同じ見出しを求める"})
	}
	return out
}
