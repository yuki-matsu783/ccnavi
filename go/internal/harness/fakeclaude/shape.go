package fakeclaude

import (
	"encoding/json"
	"regexp"
	"sort"
)

// Node は JSON の値の「形」（tests/contract/shape.py の形と同じ表。recorded.json の hooks・stream の中身）。
//
//	{"t": ["integer", "null"], "keys": {名前: Node}, "opt": [名前…], "items": Node}
//
// t は型の和集合（並べ替え済み）。keys は object のとき（キーの順は持たない）、opt はそのうち任意のもの、
// items は array の要素の形の和集合（空の配列は持たない）。
type Node struct {
	T     []string         `json:"t"`
	Keys  map[string]*Node `json:"keys,omitzero"`
	Opt   []string         `json:"opt,omitempty"`
	Items *Node            `json:"items,omitempty"`
}

const maxDepth = 5

// 環境ごとに変わる欄。値だけでなく有無も比べない。
var droppedKeys = map[string]bool{
	"avatar_url": true, "web_url": true, "username": true, "email": true,
	"public_email": true, "commit_email": true, "token": true,
}

var datetime = regexp.MustCompile(`^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)$`)

var integer = regexp.MustCompile(`^-?\d+$`)

func typeOf(v any) string {
	switch x := v.(type) {
	case nil:
		return "null"
	case bool:
		return "boolean"
	case json.Number:
		if integer.MatchString(x.String()) {
			return "integer"
		}
		return "number"
	case int, int64:
		return "integer"
	case float64:
		return "number"
	case string:
		if datetime.MatchString(x) {
			return "datetime"
		}
		return "string"
	case map[string]any:
		return "object"
	case []any:
		return "array"
	}
	panic("JSON の値ではない")
}

// Normalize は値の形を作る。数は json.Number で読んだものを渡す（整数と小数を区別するため）。
func Normalize(v any, depth int) *Node {
	kind := typeOf(v)
	node := &Node{T: []string{kind}}
	if depth >= maxDepth {
		return node
	}
	switch x := v.(type) {
	case map[string]any:
		node.Keys = map[string]*Node{}
		for k, sub := range x {
			if !droppedKeys[k] {
				node.Keys[k] = Normalize(sub, depth+1)
			}
		}
	case []any:
		for _, element := range x {
			shape := Normalize(element, depth+1)
			if node.Items == nil {
				node.Items = shape
			} else {
				node.Items = Merge(node.Items, shape)
			}
		}
	}
	return node
}

// Merge は同じ場所の 2 つの形を 1 つにする（配列の要素の和集合）。
func Merge(a, b *Node) *Node {
	out := &Node{T: union(a.T, b.T)}
	if a.Keys != nil || b.Keys != nil {
		// 片方の object にしかないキーは任意。もう片方が object でない（null など）ときは、
		// そのキーが「無い」のではなく object ごと無いので、任意にしない
		opt := map[string]bool{}
		for _, k := range append(append([]string{}, a.Opt...), b.Opt...) {
			opt[k] = true
		}
		if a.Keys != nil && b.Keys != nil {
			for k := range a.Keys {
				if _, ok := b.Keys[k]; !ok {
					opt[k] = true
				}
			}
			for k := range b.Keys {
				if _, ok := a.Keys[k]; !ok {
					opt[k] = true
				}
			}
		}
		out.Keys = map[string]*Node{}
		for k, na := range a.Keys {
			if nb, ok := b.Keys[k]; ok {
				out.Keys[k] = Merge(na, nb)
			} else {
				out.Keys[k] = na
			}
		}
		for k, nb := range b.Keys {
			if _, ok := a.Keys[k]; !ok {
				out.Keys[k] = nb
			}
		}
		for k := range opt {
			out.Opt = append(out.Opt, k)
		}
		sort.Strings(out.Opt)
	}
	switch {
	case a.Items != nil && b.Items != nil:
		out.Items = Merge(a.Items, b.Items)
	case a.Items != nil:
		out.Items = a.Items
	default:
		out.Items = b.Items
	}
	return out
}

func union(a, b []string) []string {
	seen := map[string]bool{}
	var out []string
	for _, s := range append(append([]string{}, a...), b...) {
		if !seen[s] {
			seen[s] = true
			out = append(out, s)
		}
	}
	sort.Strings(out)
	return out
}
