package testkit

import "strings"

// 親が閉じる側のテスト（lifecycle の stop・closing・integrate）が使う見本。成果物を書いた定義と、着手・完了の時刻の行を
// 置く。

// ClosingClock はこの見本を使うテストのコミットの時刻（testdata の写しの sha がこれで決まる）。
const ClosingClock Clock = "2026-01-01T00:00:00+0000"

// closingPhases は成果物を書いた定義のコピー（phases: の欄）。
const closingPhases = `phases:
  research:
    kind: work
    title: 調査
    review: false
    scope:
    - wip/research/*
    deliverables:
    - wip/research/summary.md
  design:
    kind: work
    title: 設計
    review: true
    scope:
    - wip/design/*
    - docs/*
    deliverables:
    - docs/design.md
`

// ClosingParent は親の見本。extra は frontmatter に足す行、plan は計画の行（"  - research" の形）。
func ClosingParent(id string, extra []string, plan ...string) string {
	lines := ""
	for _, e := range extra {
		lines += e + "\n"
	}
	return "---\nversion: 1\nticket: " + id + "\nmr: 1\nissue: 1\n" + closingPhases +
		"plan:\n" + strings.Join(plan, "\n") + "\n" + lines +
		"human_review:\n  required: true\n  reason: t\ntitle: 親\nrationale: r\n" +
		"allow:\n  - match: Write|Edit\n    glob: \"src/*\"\n  - match: Write|Edit\n    glob: \"wip/*\"\n---\n\n本文\n"
}

// ClosingChild は子の見本。extra は frontmatter に足す行。
func ClosingChild(id, parent, phase string, extra ...string) string {
	lines := ""
	for _, e := range extra {
		lines += e + "\n"
	}
	return "---\nversion: 1\nticket: " + id + "\nparent: " + parent + "\nphase: " + phase + "\n" +
		"human_review:\n  required: true\n  reason: t\ntitle: 子 " + id + "\nrationale: r\n" +
		"allow:\n  - match: Write|Edit\n    glob: \"wip/*\"\n" + lines + "---\n\n本文\n"
}

// 着手と完了の時刻の行（frontmatter に足す）。
const (
	ClosingStarted = `started_at: "2026-01-01T00:00:00+0900"`
	ClosingDone    = `completed_at: "2026-01-02T00:00:00+0900"`
)
