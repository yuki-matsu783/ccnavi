// Package yamlval は YAML の値の、infra/yaml に足りない扱いを持つ。Equal は Python の ==（マップはキーの順を見ず、
// 数は値で比べる。True == 1）、Without は欄を除いた写し、Step は計画のステップの書き戻す形を YAML の値にする。
// infra/yaml に入るまでの置き場（Equal は tickets/approval の sameValue と同じ答え）。
package yamlval

import (
	"math/big"
	"reflect"
	"slices"

	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/tickets/model/ticket"
)

// Equal は Python の a == b。
func Equal(a, b any) bool {
	if x, ok := number(a); ok {
		y, ok := number(b)
		return ok && x.Cmp(y) == 0
	}
	switch x := a.(type) {
	case *yaml.Map:
		y, ok := b.(*yaml.Map)
		if !ok || x.Len() != y.Len() {
			return false
		}
		for k, v := range x.All() {
			w, found := y.Lookup(k)
			if !found || !Equal(v, w) {
				return false
			}
		}
		return true
	case []any:
		y, ok := b.([]any)
		return ok && slices.EqualFunc(x, y, Equal)
	}
	return reflect.DeepEqual(a, b)
}

// Without は m から keys の欄を除いた写し（Python の {k: v for k, v in m.items() if k not in keys}）。
func Without(m *yaml.Map, keys ...string) *yaml.Map {
	out := &yaml.Map{}
	for k, v := range m.All() {
		if s, ok := k.(string); ok && slices.Contains(keys, s) {
			continue
		}
		out.Put(k, v)
	}
	return out
}

func number(v any) (*big.Float, bool) {
	switch x := v.(type) {
	case bool:
		if x {
			return big.NewFloat(1), true
		}
		return big.NewFloat(0), true
	case int:
		return new(big.Float).SetInt64(int64(x)), true
	case *big.Int:
		return new(big.Float).SetInt(x), true
	case float64:
		if x != x {
			return nil, false
		}
		return big.NewFloat(x), true
	}
	return nil, false
}

// Step は計画のステップの書き戻す形（PlanItem.Raw）を YAML の値にする（Raw は JSON のオブジェクトを返す）。
func Step(item *ticket.PlanItem) any {
	return fromJSON(item.Raw())
}

func fromJSON(v any) any {
	switch x := v.(type) {
	case *pyjson.Object:
		m := &yaml.Map{}
		for k, val := range x.All() {
			m.Put(k, fromJSON(val))
		}
		return m
	case []any:
		out := make([]any, len(x))
		for i, val := range x {
			out[i] = fromJSON(val)
		}
		return out
	}
	return v
}

// Steps は Step の並び。
func Steps(items []ticket.PlanItem) []any {
	out := make([]any, len(items))
	for i := range items {
		out[i] = Step(&items[i])
	}
	return out
}
