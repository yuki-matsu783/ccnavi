package yamljson

import (
	"math"
	"math/big"

	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/yaml"
)

// ToJSON は YAML から読んだ値を JSON の値にする（json.dumps(v, default=str) を読み直したときと同じ形）。マップは *pyjson.Object
// （キーは json.dumps がキーに書く文字列）、並び（タプルも）は []any にし、JSON に無い値（日付・集合・バイト列）は
// str() の文字列にする。
func ToJSON(v any) any {
	switch x := v.(type) {
	case nil, bool, string, int, *big.Int, float64:
		return x
	case *yaml.Map:
		out := pyjson.NewObject()
		for k, inner := range x.All() {
			out.Set(jsonKey(k), ToJSON(inner))
		}
		return out
	case []any:
		out := make([]any, len(x))
		for i, inner := range x {
			out[i] = ToJSON(inner)
		}
		return out
	case yaml.Tuple:
		return ToJSON([]any(x))
	}
	return pyfmt.Str(v)
}

// jsonKey は json.dumps がキーに書く文字列（真偽は true・false、None は null、小数の無限と NaN は
// Infinity・-Infinity・NaN）。
func jsonKey(k any) string {
	switch x := k.(type) {
	case bool:
		if x {
			return "true"
		}
		return "false"
	case nil:
		return "null"
	case float64:
		switch {
		case math.IsNaN(x):
			return "NaN"
		case math.IsInf(x, 1):
			return "Infinity"
		case math.IsInf(x, -1):
			return "-Infinity"
		}
	}
	return pyfmt.Str(k)
}

// FromJSON は上の層が JSON の形（*pyjson.Object と []any）で組んだ値を、yaml.Dump に渡せる形（*yaml.Map）にする。
// キーと値の順は保つ。
func FromJSON(v any) any {
	switch x := v.(type) {
	case *pyjson.Object:
		m := &yaml.Map{}
		for k, val := range x.All() {
			m.Put(k, FromJSON(val))
		}
		return m
	case []any:
		out := make([]any, len(x))
		for i, val := range x {
			out[i] = FromJSON(val)
		}
		return out
	}
	return v
}
