package tally

import (
	"ccnavi/internal/infra/pyfmt"
)

// recordedInt は記録の数の欄（Python の int(v or 0)）。読めない値は偽。
func recordedInt(v any) (int, bool) {
	if !pyfmt.Truthy(v) {
		return 0, true
	}
	return pyfmt.IntOf(v)
}
