package tally

import (
	"ccnavi/internal/infra/pyfmt"
)

// recordedInt は記録の数の欄。空の値は 0 と読み、数に読めない値は偽を返す。
func recordedInt(v any) (int, bool) {
	if !pyfmt.Truthy(v) {
		return 0, true
	}
	return pyfmt.IntOf(v)
}
