package samples

import (
	"fmt"
	"strings"
)

// 学びの蓄積のテストが共有する、パターンと採否の記録の文面。

const (
	// DefaultTarget・DefaultDecided は Pattern・Impact の既定の対象と決めた時刻。
	DefaultTarget  = "docs"
	DefaultDecided = "2026-01-01T00:00:00+00:00"
)

// Pattern はパターンの記録の本文。
func Pattern(sources []string, target string) string {
	return fmt.Sprintf("---\ntype: learning-wiki\ntarget: %s\nsources: [%s]\n"+
		"title: t\ndescription: d\ntags: [a]\nkeywords: [b]\n---\n\n本文\n", target, strings.Join(sources, ", "))
}

// Count はパターン名とその件数。
type Count struct {
	Name string
	N    int
}

// Impact は採否の記録の本文。patterns は書く順に並べる。
func Impact(proposal, status string, patterns []Count, decided, target string) string {
	var counts strings.Builder
	for _, c := range patterns {
		fmt.Fprintf(&counts, "  %s: %d\n", c.Name, c.N)
	}
	return fmt.Sprintf("---\nproposal: %s\ntarget: %s\nfiles: [a.md]\npatterns:\n%sstatus: %s\nreason: r\ndecided_at: '%s'\n---\n\n差分\n",
		proposal, target, counts.String(), status, decided)
}
