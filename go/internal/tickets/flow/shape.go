package flow

import (
	"fmt"
	"math"
	"math/big"
	"strconv"
	"strings"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/yaml"
)

// itemLimit は 1 ノードに並べる選択肢・分岐・次の上限。
const itemLimit = 10

// ノードの種類のうち、ここが扱いを変えるもの。
const (
	// ask は止まってメインに返すノード。サブエージェントにはユーザに聞く道具が無い。
	ask = "askUserQuestion"
	// group は図の上の囲み（ボードの枠）。手順ではないので並べない。
	group = "group"
	// loop は繰り返し。出口は branches の 2 項目（body = 繰り返す、done = 抜ける）で、上限の回数（maxIterations）を指定する。
	loop = "loop"

	kindStart        = "start"
	kindEnd          = "end"
	kindPrompt       = "prompt"
	kindSubAgent     = "subAgent"
	kindSubAgentFlow = "subAgentFlow"
	kindSkill        = "skill"
	kindMCP          = "mcp"
	kindCodex        = "codex"
	kindIfElse       = "ifElse"
	kindSwitch       = "switch"
	kindBranch       = "branch"
	kindSession      = "branchSession"

	portOutput   = "output"
	portBody     = "body"
	branchPrefix = "branch"
)

// branchKeys は出口が項目ごとに分かれる種類と、項目の欄。
var branchKeys = map[string]string{
	kindIfElse: "branches",
	kindSwitch: "branches",
	kindBranch: "branches",
	loop:       "branches",
	ask:        "options",
}

// spawn は入れ子のサブエージェントを起こすノード。
var spawn = []string{kindSubAgent, kindSubAgentFlow}

// shapeProblem は読めた中身の形の誤り（最初の 1 つ）。無ければ空。見るのは手順として並べるのに要る形だけ。
// id が無い・重なるノードは並べるときに外れるので、気づかないうちに手順が欠けないよう読まない扱いにする。
func shapeProblem(data any) string {
	top, ok := data.(*yaml.Map)
	if !ok {
		return "最上位がマッピングではない"
	}
	nodes, ok := top.List("nodes")
	if !ok {
		return "`nodes` のリストがありません。"
	}
	seen := map[string]bool{}
	for i, n := range nodes {
		node, ok := n.(*yaml.Map)
		if !ok {
			return fmt.Sprintf("nodes[%d] がマッピングではない", i)
		}
		id, ok := node.String("id")
		if !ok || id == "" {
			return fmt.Sprintf("nodes[%d] に文字列の id がありません。", i)
		}
		if seen[id] {
			return "ノードの id が重なっている（" + Line(id) + "）"
		}
		seen[id] = true
	}
	if top.Has("connections") {
		connections, ok := top.List("connections")
		if !ok {
			return "`connections` がリストではない"
		}
		for i, c := range connections {
			if _, ok := c.(*yaml.Map); !ok {
				return fmt.Sprintf("connections[%d] がマッピングではない", i)
			}
		}
	}
	return ""
}

func nodeID(node *yaml.Map) string { return textOf(node.Get("id")) }

func kindOf(node *yaml.Map) string { return textOf(node.Get("type")) }

// stepNodes は構造を見るノード。マップで id を持ち、グループでないもの（重なった id は最初の 1 つ）。
func stepNodes(data *yaml.Map) []*yaml.Map {
	var nodes []*yaml.Map
	seen := map[string]bool{}
	for _, n := range mapsIn(data.Get("nodes")) {
		if kindOf(n) == group {
			continue
		}
		if id := nodeID(n); id != "" && !seen[id] {
			seen[id] = true
			nodes = append(nodes, n)
		}
	}
	return nodes
}

// named は苦情で名指しするノード。`<id>（<名前>）`。
func named(node *yaml.Map) string {
	id, name := Line(nodeID(node)), Line(node.Get("name"))
	if name != "" && name != id {
		return id + "（" + name + "）"
	}
	return id
}

func names(nodes []*yaml.Map) string {
	shown := make([]string, 0, itemLimit)
	for _, n := range head(nodes, itemLimit) {
		shown = append(shown, named(n))
	}
	return strings.Join(capped(shown, len(nodes)), ", ")
}

// branchIndex は `branch-<番号>` の番号。番号は ASCII の数字 6 桁まで。違う表記なら偽。
func branchIndex(port string) (int, bool) {
	at := strings.LastIndex(port, "-")
	if at < 0 || port[:at] != branchPrefix {
		return 0, false
	}
	digits := port[at+1:]
	if digits == "" || len(digits) > 6 || strings.Trim(digits, "0123456789") != "" {
		return 0, false
	}
	n, _ := strconv.Atoi(digits)
	return n, true
}

// portTaken は項目（分岐・選択肢）の出口に線があるか。読み方は portLabel と同じ（項目の id か branch-<番号>）。
func portTaken(index int, item *yaml.Map, ports map[string]bool) bool {
	if id := textOf(item.Get("id")); id != "" && ports[id] {
		return true
	}
	for port := range ports {
		if n, ok := branchIndex(port); ok && n == index {
			return true
		}
	}
	return false
}

// positiveInt は 1 以上の整数か。真偽は数えず、整数の値の小数（3.0）は整数と読む。
func positiveInt(v any) bool {
	switch x := v.(type) {
	case float64:
		return x == math.Trunc(x) && !math.IsInf(x, 0) && x >= 1
	case int:
		return x >= 1
	case *big.Int:
		return x.Sign() > 0
	}
	return false
}

// StructureProblems は線の構造で問題がありそうなところ（1 件 1 行。--lint --flow の warn）。
// 見るのは最上位の nodes の手順だけで、サブフローの中は NameProblems だけが見る。出口の読み方は
// ボードの画面と同じ（複数選択の問いは output の 1 本だけ。グループへ出る線も出る側の出口は使っている）。
func StructureProblems(raw any) []string {
	// 1. 手順のノードを引ける形にする。グループは図の囲みなので、線の端として来ても手順の外として扱う。
	data := asMap(raw)
	nodes := stepNodes(data)
	byID := map[string]*yaml.Map{}
	var order []string
	for _, n := range nodes {
		byID[nodeID(n)] = n
		order = append(order, nodeID(n))
	}
	groups := map[string]bool{}
	for _, n := range mapsIn(data.Get("nodes")) {
		if kindOf(n) == group {
			groups[nodeID(n)] = true
		}
	}
	kinds := map[string]string{}
	for id, n := range byID {
		kinds[id] = kindOf(n)
	}
	// 2. 線（connections）を 1 本ずつ見る。端が無いノードを指す線は集めて、後でまとめて言う（件数が多いと読めなくなる）。
	var out, missing []string
	edges := map[string][]string{}
	edgePorts := map[string][]edgePort{}
	ports := map[string]map[string]bool{}
	for i, c := range asList(data.Get("connections")) {
		conn, ok := c.(*yaml.Map)
		if !ok {
			continue
		}
		cid := Line(conn.Get("id"))
		if cid == "" {
			cid = fmt.Sprintf("connections[%d]", i)
		}
		src, dst := textOf(conn.Get("from")), textOf(conn.Get("to"))
		bad := false
		for _, end := range [][2]string{{"from", src}, {"to", dst}} {
			if groups[end[1]] {
				continue
			}
			if _, ok := byID[end[1]]; !ok {
				shown := Line(end[1])
				if shown == "" {
					shown = "(空)"
				}
				missing = append(missing, "線 "+cid+" の "+end[0]+" が無いノード（"+shown+"）を指している")
				bad = true
			}
		}
		// 出口（port）の使用を記録する。出口の無い線は output を使ったものとして数える。
		port := textOf(conn.Get("fromPort"))
		if _, known := byID[src]; !bad && known {
			if ports[src] == nil {
				ports[src] = map[string]bool{}
			}
			taken := port
			if taken == "" {
				taken = portOutput
			}
			ports[src][taken] = true
		}
		if bad || groups[src] || groups[dst] {
			continue
		}
		edges[src] = append(edges[src], dst)
		edgePorts[src] = append(edgePorts[src], edgePort{dst, port})
		if kinds[dst] == kindStart {
			out = append(out, "線 "+cid+" が start（"+named(byID[dst])+"）に入っている")
		}
		if kinds[src] == kindEnd {
			out = append(out, "線 "+cid+" が end（"+named(byID[src])+"）から出ている")
		}
	}
	// 3. start と end が 1 つ以上あるか。
	out = append(out, head(missing, itemLimit)...)
	if len(missing) > itemLimit {
		out = append(out, fmt.Sprintf("無いノードを指す線は…ほか %d 件", len(missing)-itemLimit))
	}
	var starts []string
	hasEnd := false
	for _, id := range order {
		switch kinds[id] {
		case kindStart:
			starts = append(starts, id)
		case kindEnd:
			hasEnd = true
		}
	}
	if len(starts) == 0 {
		out = append(out, "start がありません（どこから始めるかが決まらない）")
	}
	if !hasEnd {
		out = append(out, "end がありません（どこで終わるかが決まらない）")
	}
	// 4. start から線をたどって、届かないノードを挙げる（幅優先）。
	if len(starts) > 0 {
		reached := map[string]bool{}
		queue := append([]string(nil), starts...)
		for len(queue) > 0 {
			current := queue[0]
			queue = queue[1:]
			if reached[current] {
				continue
			}
			reached[current] = true
			queue = append(queue, edges[current]...)
		}
		var lost []*yaml.Map
		for _, id := range order {
			if !reached[id] {
				lost = append(lost, byID[id])
			}
		}
		if len(lost) > 0 {
			out = append(out, "start から届かないノードがある: "+names(lost))
		}
	}
	// 5. 問い・分岐・繰り返しの出口のうち、線が出ていないものを挙げる。複数選択の問いは output の 1 本だけが出口。
	for _, id := range order {
		node := byID[id]
		key, ok := branchKeys[kinds[id]]
		if !ok {
			continue
		}
		taken := ports[id]
		info := asMap(node.Get("data"))
		if kinds[id] == ask && info.Get("multiSelect") == true {
			if !taken[portOutput] {
				out = append(out, "問い "+named(node)+" の出口に線が無い: output（複数選択）")
			}
			continue
		}
		var empty []string
		for i, item := range mapsIn(info.Get(key)) {
			if portTaken(i, item, taken) {
				continue
			}
			label := Line(item.Get("label"))
			if label == "" {
				label = fmt.Sprintf("%d 番目", i+1)
			}
			empty = append(empty, label)
		}
		if len(empty) > 0 {
			what := "分岐"
			switch kinds[id] {
			case ask:
				what = "問い"
			case loop:
				what = "繰り返し"
			}
			shown := strings.Join(capped(head(empty, itemLimit), len(empty)), ", ")
			out = append(out, what+" "+named(node)+" の出口に線が無い: "+shown)
		}
	}
	return append(out, loopProblems(order, byID, kinds, edgePorts)...)
}

type edgePort struct{ to, port string }

// loopProblems は loop の条件・上限の回数・出口と、繰り返す側を通らない巡回（1 件 1 行）。
func loopProblems(order []string, byID map[string]*yaml.Map, kinds map[string]string, edgePorts map[string][]edgePort) []string {
	var out []string
	for _, id := range order {
		if kinds[id] != loop {
			continue
		}
		node := byID[id]
		info := asMap(node.Get("data"))
		if ospath.Strip(textOf(info.Get("condition"))) == "" {
			out = append(out, "繰り返し "+named(node)+" の condition が空"+
				"（何が成り立つあいだ繰り返すかが決まらない）")
		}
		if !positiveInt(info.Get("maxIterations")) {
			out = append(out, "繰り返し "+named(node)+" の maxIterations が 1 以上の整数ではない"+
				"（回数の上限が無いと、いつ終えるかを判断できなくなる）")
		}
		if len(mapsIn(info.Get("branches"))) != 2 {
			out = append(out, "繰り返し "+named(node)+" の出口（branches）が 2 つではない（繰り返す・抜ける）")
		}
	}
	return append(out, cycleProblems(order, byID, kinds, edgePorts)...)
}

// isBodyPort は loop の出口が「繰り返す」側か。body・branch-0・1 つ目の項目の id のどれか。
func isBodyPort(node *yaml.Map, port string) bool {
	if port == portBody || port == branchPrefix+"-0" {
		return true
	}
	items := mapsIn(asMap(node.Get("data")).Get("branches"))
	if len(items) == 0 {
		return false
	}
	first := textOf(items[0].Get("id"))
	return first != "" && port == first
}

// cycleProblems は繰り返す側（loop の body の出口）を通らない巡回。繰り返す側の線は上限の回数で止まる
// 手がかりなので外して巡回を探す。巡回の上にあるのは、強連結成分（Tarjan）の大きさが 2 以上のものと、
// 自分に戻る線のあるノード。
func cycleProblems(order []string, byID map[string]*yaml.Map, kinds map[string]string, edgePorts map[string][]edgePort) []string {
	// 1. 巡回を探す有向グラフを組む。loop の「繰り返す」側の線は上限の回数で止まるので、入れない。
	graph := map[string][]string{}
	for _, id := range order {
		graph[id] = nil
	}
	onCycle := map[string]bool{}
	for src, pairs := range edgePorts {
		for _, e := range pairs {
			if _, ok := graph[e.to]; !ok || (kinds[src] == loop && isBodyPort(byID[src], e.port)) {
				continue
			}
			graph[src] = append(graph[src], e.to)
			if src == e.to {
				onCycle[src] = true
			}
		}
	}
	// 2. 強連結成分を Tarjan 法で求める。再帰だと深いフローで深く潜るので、自前の stack（work）で反復する。
	index, low := map[string]int{}, map[string]int{}
	onStack := map[string]bool{}
	var stack []string
	counter := 0
	type frame struct {
		node string
		i    int
	}
	for _, root := range order {
		if _, seen := index[root]; seen {
			continue
		}
		// frame は「node の隣を i 番目まで見た」状態。子へ降りるときは、戻ってこられるよう自分を積み直してから子を積む。
		work := []frame{{root, 0}}
		for len(work) > 0 {
			f := work[len(work)-1]
			work = work[:len(work)-1]
			node, i := f.node, f.i
			if i == 0 {
				index[node], low[node] = counter, counter
				counter++
				stack = append(stack, node)
				onStack[node] = true
			}
			advanced := false
			for i < len(graph[node]) {
				next := graph[node][i]
				i++
				if _, seen := index[next]; !seen {
					work = append(work, frame{node, i}, frame{next, 0})
					advanced = true
					break
				}
				if onStack[next] {
					low[node] = min(low[node], index[next])
				}
			}
			if advanced {
				continue
			}
			// node が成分の根。stack から根までを取り出して 1 つの成分にする。
			if low[node] == index[node] {
				var members []string
				for {
					top := stack[len(stack)-1]
					stack = stack[:len(stack)-1]
					onStack[top] = false
					members = append(members, top)
					if top == node {
						break
					}
				}
				if len(members) > 1 {
					for _, m := range members {
						onCycle[m] = true
					}
				}
			}
			if len(work) > 0 {
				parent := work[len(work)-1].node
				low[parent] = min(low[parent], low[node])
			}
		}
	}
	// 3. 大きさ 2 以上の成分と、自分に戻る線のあるノードが巡回の上にある。
	if len(onCycle) == 0 {
		return nil
	}
	var on []*yaml.Map
	for _, id := range order {
		if onCycle[id] {
			on = append(on, byID[id])
		}
	}
	return []string{"繰り返し（loop の「繰り返す」側）を通らない巡回がある: " + names(on) +
		"（繰り返すなら loop の「繰り返す」側へ戻し、回数の上限を決める）"}
}

// flowNodes は名前を確かめるノード。最上位とサブフローの中の両方。
func flowNodes(raw any) []*yaml.Map {
	data := asMap(raw)
	nodes := mapsIn(data.Get("nodes"))
	for _, sub := range asList(data.Get("subAgentFlows")) {
		nodes = append(nodes, mapsIn(asMap(sub).Get("nodes"))...)
	}
	return nodes
}

// NameProblems は subAgent の種類（builtInType）と skill の名前が候補に無いもの（1 件 1 行）。空の欄
// （書きかけ）と `:` を含むスキル（プラグインのもの）は言わない。大文字小文字だけが違えば正しい表記をつける。
func NameProblems(raw any, cat Catalog) []string {
	agents, skills := cat.agents.names(), cat.skills.names()
	var out []string
	for _, node := range flowNodes(raw) {
		info := asMap(node.Get("data"))
		var what, value string
		var known []string
		switch kindOf(node) {
		case kindSubAgent:
			what, value, known = "サブエージェントの種類", textOf(info.Get("builtInType")), agents
		case kindSkill:
			what, value, known = "スキル", textOf(info.Get("name")), skills
			if strings.Contains(value, ":") {
				continue
			}
		default:
			continue
		}
		value = ospath.Strip(value)
		if value == "" || contains(known, value) {
			continue
		}
		hint := ""
		for _, k := range known {
			if pyfmt.Casefold(k) == pyfmt.Casefold(value) {
				hint = "。大文字小文字が違う（" + Line(k) + "）"
				break
			}
		}
		out = append(out, "ノード "+named(node)+" の"+what+" "+Line(value)+" が候補にありません"+
			"（組み込みと .claude/ の下にありません。書き誤りかもしれない。"+
			"ユーザ・プラグインのものなら気にしなくてよい）"+hint)
	}
	return out
}

func contains(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}
