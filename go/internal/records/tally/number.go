package tally

import (
	"math"
	"strconv"
	"strings"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
)

// pyInt は Python の int(text.strip())。数字の間の "_" を 1 つずつ受ける。
func pyInt(text string) (int, bool) {
	text = ospath.Strip(text)
	digits := strings.TrimLeft(text, "+-")
	if len(text)-len(digits) > 1 || digits == "" || digits[0] == '_' || digits[len(digits)-1] == '_' ||
		strings.Contains(digits, "__") {
		return 0, false
	}
	n, err := strconv.Atoi(text[:len(text)-len(digits)] + strings.ReplaceAll(digits, "_", ""))
	return n, err == nil
}

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
		return pyInt(x)
	}
	return 0, false
}
