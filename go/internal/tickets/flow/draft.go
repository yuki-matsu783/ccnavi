package flow

import "ccnavi/internal/infra/pyjson"

// layoutKeys はノードの見た目だけの欄（位置・グループの大きさと所属）。担当に渡る手順に効かない。
var layoutKeys = map[string]bool{"position": true, "style": true, "parentId": true}

// groupType はグループの枠のノードの種類。枠は見た目だけなので見比べない。
const groupType = "group"

// DraftDiffers は子のフローの下書きを「提案あり」として見せるか（チケット管理画面の flow.draft.differs）。下書きが在り、
// 読めないか、いまのフローと位置とグループ化を除いて違えば真。いまのフローが無ければ、下書きが在るだけで真。
// いまのフローが読めなければ偽（フロー編集の画面が開けないので、見比べる相手が無い）。
func DraftDiffers(info *Info) bool {
	if info == nil || !info.Draft.Exists {
		return false
	}
	var current any
	if info.Exists {
		data, ok := readDoc(info.Path, info.Tree)
		if !ok {
			return false
		}
		current = data
	}
	draft, ok := readDoc(info.Draft.Path, info.Tree)
	return !ok || current == nil || !SameIgnoringLayout(current, draft)
}

// readDoc は図に描ける形のフロー（最上位がマップで、nodes が文字列の id を持つマップのリスト）を読む。
// 形の細かい検査（shapeProblem）はしない。フロー編集の画面が図にできるかと同じ線で切る。
func readDoc(file, treeRoot string) (any, bool) {
	raw, _ := ReadBytes(file, treeRoot)
	if raw == nil {
		return nil, false
	}
	data, why := ParseYAML(raw)
	if why != "" {
		return nil, false
	}
	nodes, ok := asMap(data).List("nodes")
	if !ok {
		return nil, false
	}
	for _, n := range nodes {
		m := asMap(n)
		if m == nil {
			return nil, false
		}
		if _, ok := m.Get("id").(string); !ok {
			return nil, false
		}
	}
	return data, true
}

// SameIgnoringLayout は 2 つのフローが、位置とグループ化（ノードの position・style・parentId と、type が group の
// ノード。subAgentFlows の中も）を除いて同じ中身か。マップはキーの順を見ず、リストは順も見る。値は --lint --json --flow の
// flow.data と同じ形（AsJSON）にしてから比べる。
func SameIgnoringLayout(a, b any) bool {
	return sameJSON(withoutLayout(AsJSON(a)), withoutLayout(AsJSON(b)))
}

// withoutLayout は位置とグループを除いたフロー。nodes と、subAgentFlows の各フローの nodes から除く。
func withoutLayout(v any) any {
	doc, ok := v.(*pyjson.Object)
	if !ok {
		return v
	}
	out := copyObject(doc)
	if nodes, ok := doc.Value("nodes").([]any); ok {
		out.Set("nodes", nodesWithoutLayout(nodes))
	}
	if flows, ok := doc.Value("subAgentFlows").([]any); ok {
		subs := make([]any, len(flows))
		for i, f := range flows {
			subs[i] = f
			if sub, ok := f.(*pyjson.Object); ok {
				if nodes, ok := sub.Value("nodes").([]any); ok {
					c := copyObject(sub)
					c.Set("nodes", nodesWithoutLayout(nodes))
					subs[i] = c
				}
			}
		}
		out.Set("subAgentFlows", subs)
	}
	return out
}

func nodesWithoutLayout(nodes []any) []any {
	out := []any{}
	for _, n := range nodes {
		node, ok := n.(*pyjson.Object)
		if !ok {
			out = append(out, n)
			continue
		}
		if kind, _ := node.Value("type").(string); kind == groupType {
			continue
		}
		kept := pyjson.NewObject()
		for _, k := range node.Keys() {
			if !layoutKeys[k] {
				kept.Set(k, node.Value(k))
			}
		}
		out = append(out, kept)
	}
	return out
}

func copyObject(o *pyjson.Object) *pyjson.Object {
	out := pyjson.NewObject()
	for _, k := range o.Keys() {
		out.Set(k, o.Value(k))
	}
	return out
}

// sameJSON は AsJSON の値が同じか。オブジェクトはキーの順を見ない。
func sameJSON(a, b any) bool {
	switch x := a.(type) {
	case *pyjson.Object:
		y, ok := b.(*pyjson.Object)
		if !ok || x.Len() != y.Len() {
			return false
		}
		for _, k := range x.Keys() {
			other, found := y.Get(k)
			if !found || !sameJSON(x.Value(k), other) {
				return false
			}
		}
		return true
	case []any:
		y, ok := b.([]any)
		if !ok || len(x) != len(y) {
			return false
		}
		for i := range x {
			if !sameJSON(x[i], y[i]) {
				return false
			}
		}
		return true
	}
	switch b.(type) {
	case *pyjson.Object, []any:
		return false
	}
	return a == b
}
