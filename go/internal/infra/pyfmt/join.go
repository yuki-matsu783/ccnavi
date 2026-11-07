package pyfmt

import (
	"slices"
	"strings"
)

// ParagraphBreak は文と文の区切り（空行）。段落は JoinNonEmpty(ParagraphBreak, …) でつなぐ。
const ParagraphBreak = "\n\n"

// Repeats は NonEmpty が同じ文字列の 2 度目以降をどう扱うか。
type Repeats int

const (
	// KeepRepeats は重なりも残す。
	KeepRepeats Repeats = iota
	// DropRepeats は最初に出たものだけを残す（順は保つ）。
	DropRepeats
)

// NonEmpty は空でない文字列を順に並べる。
func NonEmpty(items []string, repeats Repeats) []string {
	var out []string
	for _, s := range items {
		if s != "" && (repeats == KeepRepeats || !slices.Contains(out, s)) {
			out = append(out, s)
		}
	}
	return out
}

// JoinNonEmpty は空でない文字列を sep でつなぐ（sep.join(p for p in parts if p)）。
func JoinNonEmpty(sep string, parts ...string) string {
	return strings.Join(NonEmpty(parts, KeepRepeats), sep)
}
