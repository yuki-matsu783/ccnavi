package tally

import (
	"math"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
)

// recordedInt は記録の数の欄（Python の int(v or 0)）。読めない値は偽。
func recordedInt(v any) (int, bool) {
	if !pyfmt.Truthy(v) {
		return 0, true
	}
	switch x := v.(type) {
	case bool:
		return 1, true
	case int:
		return x, true
	case float64:
		if math.IsInf(x, 0) || math.IsNaN(x) {
			return 0, false
		}
		return int(x), true
	case string:
		n, err := pyfmt.ParseInt(ospath.Strip(x))
		return n, err == nil
	}
	return 0, false
}
