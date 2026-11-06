package pyfmt

import "strings"

// Lower は Python の str.lower()。strings.ToLower と答えが違う U+0130（İ。Python は "i̇" の 2 字、Go は "i"）を合わせる。
// 語末の Σ（Final_Sigma）は合わせない。比べる相手は ASCII の語（モード・id の頭・コマンド名）なので答えは変わらない。
func Lower(s string) string {
	return strings.ToLower(strings.ReplaceAll(s, "İ", "i̇"))
}
