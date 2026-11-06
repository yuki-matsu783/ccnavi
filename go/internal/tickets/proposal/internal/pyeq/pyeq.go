// Package pyeq は YAML から読んだ値を Python の == で比べる（マップはキーの順を見ず、数は値で比べる。True == 1）。
// infra/yaml に Equal が入るまでの置き場（tickets/approval の sameValue と同じ答え）。
package pyeq

import (
	"math/big"
	"reflect"
	"slices"

	"ccnavi/internal/infra/yaml"
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
