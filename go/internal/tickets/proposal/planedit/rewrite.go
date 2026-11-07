package planedit

import (
	"fmt"
	"slices"
	"strings"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/proposal/internal/yamlval"
)

// frontmatter の欄の値の行だけを差し替える部品。ほかの行（コメントも）と本文は変えない。

// front は frontmatter の行（改行付き）、閉じる囲いの行の位置、書き足す行の改行。
type front struct {
	lines   []string
	end     int
	newline string
}

func frontOf(text string) (front, string) {
	lines := ospath.SplitlinesKeep(text)
	if len(lines) == 0 || ospath.Strip(lines[0]) != ticket.Fence {
		return front{}, "frontmatter が読めない"
	}
	end := slices.IndexFunc(lines[1:], func(l string) bool { return ospath.Strip(l) == ticket.Fence })
	if end < 0 {
		return front{}, "frontmatter が閉じていない"
	}
	newline := "\n"
	if strings.HasSuffix(lines[0], "\r\n") {
		newline = "\r\n"
	}
	return front{lines, end + 1, newline}, ""
}

// keyLine は行が `key:`（key と ":" の間は空白だけ）で始まるか。Python の re.match(rf"^{key}\s*:")。
func keyLine(line, key string) bool {
	rest, ok := strings.CutPrefix(line, key)
	return ok && strings.HasPrefix(strings.TrimLeftFunc(rest, ospath.IsSpace), ":")
}

func indentedLine(line string) bool { return line[0] == ' ' || line[0] == '\t' }

// valueEnd は start の行の欄の値が終わる位置（値のあとの空行は値の外に残す）。dashes は行頭の "-" も値の続きと
// 読むか（ブロックの列を字下げせずに書く形）。値の中に行頭のコメントがあって範囲が決まらなければ理由を返す。
func (f front) valueEnd(start int, key string, dashes, dashAfter bool) (int, string) {
	stop := start + 1
	for stop < f.end {
		line := f.lines[stop]
		if ospath.Strip(line) == "" || indentedLine(line) || (dashes && strings.HasPrefix(line, "-")) {
			stop++
			continue
		}
		break
	}
	for stop-1 > start && ospath.Strip(f.lines[stop-1]) == "" {
		stop--
	}
	after := stop
	for after < f.end && (ospath.Strip(f.lines[after]) == "" || strings.HasPrefix(f.lines[after], "#")) {
		after++
	}
	if after > stop && after < f.end && (indentedLine(f.lines[after]) || (dashAfter && strings.HasPrefix(f.lines[after], "-"))) {
		return 0, "`" + key + ":` の値の中に行頭（0 桁目）のコメントがあり、値の範囲が決まらない。" +
			"コメントを字下げするか削除してから再実行します。"
	}
	return stop, ""
}

func (f front) replaced(start, stop int, block []string) string {
	var b strings.Builder
	for _, l := range f.lines[:start] {
		b.WriteString(l)
	}
	for _, l := range block {
		b.WriteString(l + f.newline)
	}
	for _, l := range f.lines[stop:] {
		b.WriteString(l)
	}
	return b.String()
}

// replaceBlock は frontmatter の `key:` の行と値を block（改行なし）に差し替える。`key:` の行は 1 つだけのこと。
func replaceBlock(text, key string, block []string) (string, string) {
	f, why := frontOf(text)
	if why != "" {
		return text, why
	}
	var heads []int
	for i := 1; i < f.end; i++ {
		if keyLine(f.lines[i], key) {
			heads = append(heads, i)
		}
	}
	if len(heads) != 1 {
		return text, "`" + key + ":` の行が 1 つに決まらない"
	}
	start := heads[0]
	_, value, _ := strings.Cut(f.lines[start], ":")
	value, _, _ = strings.Cut(value, "#")
	stop, why := f.valueEnd(start, key, ospath.Strip(value) == "", true)
	if why != "" {
		return text, why
	}
	return f.replaced(start, stop, block), ""
}

// body は Python の text.split("---", 2)[2:]（frontmatter のあと）。
func body(text string) []string {
	parts := strings.SplitN(text, ticket.Fence, 3)
	if len(parts) < 3 {
		return nil
	}
	return parts[2:]
}

// changedElsewhere は書き換えた全文を読み直し、keys のほかの欄と本文が元と同じか。同じなら読み直した frontmatter、
// 違えば理由（書かない）。values は理由の文で書き換えた値を指す言葉。値にアンカーがあり、ほかの欄が別名で指して
// いると、値を差し替えたときにほかの欄も変わるので、読んだ形で比べて見つける。
func changedElsewhere(old, new string, keys []string, values string) (*yaml.Map, string) {
	before, err := ticket.FrontMapping(old)
	if err == nil {
		var after *yaml.Map
		after, err = ticket.FrontMapping(new)
		if err == nil {
			if before == nil || after == nil {
				return nil, "組んだ全文の frontmatter が読めない"
			}
			if !yaml.Equal(before.Without(keys...), after.Without(keys...)) {
				return nil, "差し替えると " + strings.Join(keys, "・") + " のほかの欄が変わる（" + values + "にアンカーがあり、" +
					"ほかの欄が別名で指しているなど）ので反映しません"
			}
			if !slices.Equal(body(old), body(new)) {
				return nil, "差し替えると本文が変わるので反映しません"
			}
			return after, ""
		}
	}
	return nil, "組んだ全文を読み直せない（" + err.Error() + "）。" + values + "をほかの欄が別名で指していないか確かめる"
}

// flowLine は値を 1 行の flow 形式で書く（yaml.safe_dump(…, default_flow_style=True, width=1 << 30).strip()）。
func flowLine(v any) (string, error) {
	dumped, err := yaml.Dump(v, yaml.DumpOptions{Flow: true, Width: 1 << 30})
	return ospath.Strip(dumped), err
}

// stepText は計画のステップ 1 つの flow 形式（列の括弧を外したもの）。
func stepText(item *ticket.PlanItem) (string, error) {
	line, err := flowLine([]any{yamlval.Step(item)})
	if err != nil {
		return "", err
	}
	return ospath.Strip(line[1 : len(line)-1]), nil
}

// replaceSteps は frontmatter の `key:` の値をステップの並びに差し替える。
func replaceSteps(text, key string, items []ticket.PlanItem) (string, string) {
	if len(items) == 0 {
		return replaceBlock(text, key, []string{key + ": []"})
	}
	block := []string{key + ":"}
	for i := range items {
		line, err := stepText(&items[i])
		if err != nil {
			return text, err.Error()
		}
		block = append(block, "  - "+line)
	}
	return replaceBlock(text, key, block)
}

var planKeys = []string{string(phases.Plan), string(phases.FeedbackPart)}

// rebuilt は親の提案の plan: / feedback: の値だけを振り直した形に差し替えたバイト列。変わらなければ (nil, "")、
// 書けなければ (nil, 理由)。
func (a *Answer) rebuilt() ([]byte, string) {
	parent, revised := a.source.Parent, a.revised
	type change struct {
		key   string
		items []ticket.PlanItem
	}
	var changed []change
	for _, c := range []struct {
		key      string
		old, new []ticket.PlanItem
	}{{planKeys[0], parent.Plan, revised.Plan}, {planKeys[1], parent.Feedback, revised.Feedback}} {
		if !yaml.Equal(yamlval.Steps(c.old), yamlval.Steps(c.new)) {
			changed = append(changed, change{c.key, c.new})
		}
	}
	if len(changed) == 0 {
		return nil, ""
	}
	text, ok := decode(a.source.raw)
	if !ok {
		return nil, "UTF-8 として読めない"
	}
	built := text
	for _, c := range changed {
		var why string
		if built, why = replaceSteps(built, c.key, c.items); why != "" {
			return nil, why
		}
	}
	if why := planChangedElsewhere(text, built, revised); why != "" {
		return nil, why
	}
	return []byte(built), ""
}

type stepShape struct {
	typ      string
	review   ticket.Need
	deferred bool
	after    string
}

func shapes(items []ticket.PlanItem) []stepShape {
	out := make([]stepShape, len(items))
	for i, it := range items {
		out[i] = stepShape{it.Type, it.Review, it.Deferred, fmt.Sprint(it.After)}
	}
	return out
}

// planChangedElsewhere は組んだ全文を読み直し、計画のほかの欄と本文が元と同じで、計画が振り直した計画と合うか。
func planChangedElsewhere(old, new string, revised *ticket.Ticket) string {
	if _, why := changedElsewhere(old, new, planKeys, "`plan:` / `feedback:` の値"); why != "" {
		return why
	}
	read, _ := ticket.Parse(new)
	if read == nil {
		return "組んだ全文がチケットとして読めないので反映しません"
	}
	if !slices.Equal(shapes(read.Plan), shapes(revised.Plan)) || !slices.Equal(shapes(read.Feedback), shapes(revised.Feedback)) {
		return "組んだ全文の計画が振り直した計画と合わないので反映しません"
	}
	return ""
}
