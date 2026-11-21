package gitlabapi

import (
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
)

// AsMap は本文がオブジェクトならそれ、でなければ nil。
func AsMap(v any) map[string]any {
	m, _ := v.(map[string]any)
	return m
}

// AsList は本文が配列ならそれ、でなければ nil。
func AsList(v any) []any {
	l, _ := v.([]any)
	return l
}

// Text は文字列ならそれ、でなければ空。
func Text(v any) string {
	s, _ := v.(string)
	return s
}

// Int は整数の値（JSON の数）を取り出す。数でなければ ok は false。
func Int(v any) (int, bool) {
	switch n := v.(type) {
	case json.Number:
		i, err := strconv.Atoi(n.String())
		return i, err == nil
	case float64:
		return int(n), true
	case int:
		return n, true
	}
	return 0, false
}

// HasMarker はメモのどれかの本文が marker で始まるか。
func HasMarker(notes []any, marker string) bool {
	for _, n := range notes {
		if strings.HasPrefix(Text(AsMap(n)["body"]), marker) {
			return true
		}
	}
	return false
}

// FirstNoteID はスレッドの最初のメモの id。無ければ空。
func FirstNoteID(discussion map[string]any) string {
	notes := AsList(discussion["notes"])
	if len(notes) == 0 {
		return ""
	}
	return fmt.Sprint(AsMap(notes[0])["id"])
}
