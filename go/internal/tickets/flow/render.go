package flow

import (
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"

	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/yaml"
)

// 並べる上限。多ければサブエージェントにファイルを読ませる。
const (
	// renderLimit は並べるノードの数。
	renderLimit = 40
	// ChildTextLimit は子 1 本の手順の文の上限（文字）。
	ChildTextLimit = 4000
	// TotalTextLimit は SubagentStart 全体でフローに使う文の上限（文字）。
	TotalTextLimit = 12000
)

func labels(items any, key string) []string {
	entries := mapsIn(items)
	out := make([]string, 0, itemLimit)
	for _, e := range head(entries, itemLimit) {
		out = append(out, Line(e.Get(key)))
	}
	return capped(out, len(entries))
}

// summary はノード 1 つの中身を 1 行で。知らない種類は空。
func summary(node *yaml.Map, flows map[string]*yaml.Map) string {
	data := asMap(node.Get("data"))
	switch kind := kindOf(node); kind {
	case kindPrompt:
		return Line(data.Get("prompt"))
	case kindSubAgent:
		parts := []string{Line(data.Get("description"))}
		if p := Line(data.Get("prompt")); p != "" {
			parts = append(parts, "プロンプト: "+p)
		}
		if t := Line(data.Get("builtInType")); t != "" {
			parts = append(parts, "種類: "+t)
		}
		return pyfmt.JoinNonEmpty(" / ", parts...)
	case ask:
		options := strings.Join(labels(data.Get("options"), "label"), " | ")
		multi := ""
		if data.Get("multiSelect") == true {
			multi = "（複数選択）"
		}
		return "問い: " + Line(data.Get("questionText")) + " 選択肢" + multi + ": " + options
	case kindIfElse, kindSwitch, kindBranch:
		branches := mapsIn(data.Get("branches"))
		var parts []string
		for _, b := range head(branches, itemLimit) {
			parts = append(parts, Line(b.Get("label"))+"="+Line(b.Get("condition")))
		}
		parts = capped(parts, len(branches))
		target := Line(data.Get("evaluationTarget"))
		if target != "" {
			target += ": "
		}
		return target + strings.Join(parts, " | ")
	case loop:
		limit := data.Get("maxIterations")
		bound := "最大回数が書かれていない（1 以上の整数でない）"
		if positiveInt(limit) {
			bound = "最大 " + textOf(limit) + " 回"
		}
		return "条件: " + Line(data.Get("condition")) + " / " + bound
	case kindSkill:
		return "スキル " + Line(data.Get("name")) + ": " + Line(data.Get("description"))
	case kindMCP:
		out := "MCP " + Line(data.Get("serverId"))
		if tool := Line(data.Get("toolName")); tool != "" {
			out += " / " + tool
		}
		return out
	case kindSubAgentFlow:
		name := ""
		if ref, ok := flows[textOf(data.Get("subAgentFlowId"))]; ok {
			name = Line(ref.Get("name"))
		}
		out := Line(data.Get("label"))
		if out == "" {
			out = name
		}
		if name != "" {
			out += "（サブフロー " + name + "）"
		}
		return out
	case kindCodex:
		return "Codex: " + Line(data.Get("prompt"))
	case kindSession, kindStart, kindEnd:
		return Line(data.Get("label"))
	}
	return ""
}

// portLess は出口の並べ順。branch-<番号> は番号の順で前、それ以外は文字列の順で後ろ。
func portLess(a, b *yaml.Map) bool {
	pa, pb := textOf(a.Get("fromPort")), textOf(b.Get("fromPort"))
	ia, oka := branchIndex(pa)
	ib, okb := branchIndex(pb)
	switch {
	case oka && okb:
		return ia < ib
	case oka != okb:
		return oka
	}
	return pa < pb
}

// portLabel は分岐の出口の名前。出口が項目の id とちょうど同じか、branch-<番号> の番号が項目の位置なら
// その label（部分一致は採らない）。ボードの線の言葉と同じ読み方。
func portLabel(node *yaml.Map, port string) string {
	key, ok := branchKeys[kindOf(node)]
	if port == "" || !ok {
		return ""
	}
	items := mapsIn(asMap(node.Get("data")).Get(key))
	for _, item := range items {
		if id := textOf(item.Get("id")); id != "" && id == port {
			return Line(item.Get("label"))
		}
	}
	if n, ok := branchIndex(port); ok && n < len(items) {
		return Line(items[n].Get("label"))
	}
	return ""
}

// order は並べる順。start から幅優先で辿り、辿れなかったものは後ろに元の順で足す。
func order(ids []string, kinds map[string]string, out map[string][]*yaml.Map) []string {
	known := map[string]bool{}
	var starts []string
	for _, id := range ids {
		known[id] = true
		if kinds[id] == kindStart {
			starts = append(starts, id)
		}
	}
	if len(starts) == 0 {
		starts = head(ids, 1)
	}
	seen := map[string]bool{}
	var visited []string
	queue := append([]string(nil), starts...)
	for len(queue) > 0 {
		current := queue[0]
		queue = queue[1:]
		if seen[current] || !known[current] {
			continue
		}
		seen[current] = true
		visited = append(visited, current)
		for _, c := range out[current] {
			queue = append(queue, textOf(c.Get("to")))
		}
	}
	for _, id := range ids {
		if !seen[id] {
			visited = append(visited, id)
		}
	}
	return visited
}

// Render はフローを順に並べた行と、出てきたノードの種類の集合。YAML を読まなくても手順が追えるよう、
// 1 ノード 1 行で `<番号>. [<種類>] <名前>: <中身> → <次の番号>` を並べる。ノードは renderLimit 件まで、
// 文は textLimit 文字まで（ChildTextLimit を渡すのがふつう）。
func Render(raw any, textLimit int) ([]string, map[string]bool) {
	data := asMap(raw)
	var nodes []*yaml.Map
	seenIDs := map[string]bool{}
	for _, n := range mapsIn(data.Get("nodes")) {
		// グループは図の上の囲みで手順ではない。並べない（線が繋がっていても辿らない）
		if kindOf(n) == group {
			continue
		}
		if id := nodeID(n); id != "" && !seenIDs[id] {
			seenIDs[id] = true
			nodes = append(nodes, n)
		}
	}
	flows := map[string]*yaml.Map{}
	for _, f := range mapsIn(data.Get("subAgentFlows")) {
		if id := textOf(f.Get("id")); id != "" {
			flows[id] = f
		}
	}
	byID := map[string]*yaml.Map{}
	var ids []string
	kindsBy := map[string]string{}
	for _, n := range nodes {
		id := nodeID(n)
		byID[id] = n
		ids = append(ids, id)
		kindsBy[id] = kindOf(n)
	}
	out := map[string][]*yaml.Map{}
	for _, c := range mapsIn(data.Get("connections")) {
		from := textOf(c.Get("from"))
		out[from] = append(out[from], c)
	}
	for _, edges := range out {
		sort.SliceStable(edges, func(i, j int) bool { return portLess(edges[i], edges[j]) })
	}
	ordered := order(ids, kindsBy, out)
	number := map[string]int{}
	for i, id := range ordered {
		number[id] = i + 1
	}
	kinds := map[string]bool{}
	for _, k := range kindsBy {
		kinds[k] = true
	}
	var lines []string
	used, shown := 0, 0
	for _, id := range head(ordered, renderLimit) {
		node := byID[id]
		kind := Line(kindsBy[id])
		if kind == "" {
			kind = "?"
		}
		name := Line(node.Get("name"))
		if name == "" {
			name = Line(id)
		}
		text := strconv.Itoa(number[id]) + ". [" + kind + "] " + name
		if body := summary(node, flows); body != "" {
			text += ": " + body
		}
		var edges []*yaml.Map
		for _, c := range out[id] {
			if _, ok := number[textOf(c.Get("to"))]; ok {
				edges = append(edges, c)
			}
		}
		var nexts []string
		for _, c := range head(edges, itemLimit) {
			label := Line(c.Get("condition"))
			if label == "" {
				label = portLabel(node, textOf(c.Get("fromPort")))
			}
			next := strconv.Itoa(number[textOf(c.Get("to"))])
			if label != "" {
				next += "（" + label + "）"
			}
			nexts = append(nexts, next)
		}
		if nexts = capped(nexts, len(edges)); len(nexts) > 0 {
			text += " → " + strings.Join(nexts, ", ")
		}
		// 組み立てた行でも見る。種類の名前が ccnavi… だと、こちらの [<種類>] が接頭辞になる。
		text = neutral(text)
		size := utf8.RuneCountInString(text)
		if used+size > textLimit && len(lines) > 0 {
			break
		}
		lines = append(lines, text)
		used += size
		shown++
	}
	if len(ordered) > shown {
		lines = append(lines, "…ほか "+strconv.Itoa(len(ordered)-shown)+" 件。続きはファイルを読んでください")
	}
	return lines, kinds
}
