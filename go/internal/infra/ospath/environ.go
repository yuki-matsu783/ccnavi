package ospath

import "strings"

// WithoutEnv は環境（"名前=値" の並び）から names の変数を外した写し。名前は OS の決まりで比べる
// （Windows は大文字小文字を区別しない。どの機械でも区別しない pathcase.Fold とは違う）。
func WithoutEnv(environ []string, names []string) []string {
	out := make([]string, 0, len(environ))
	for _, kv := range environ {
		name, _, _ := strings.Cut(kv, "=")
		if !envNamed(names, name) {
			out = append(out, kv)
		}
	}
	return out
}

func envNamed(names []string, name string) bool {
	for _, n := range names {
		if n == name || (Windows && strings.EqualFold(n, name)) {
			return true
		}
	}
	return false
}
