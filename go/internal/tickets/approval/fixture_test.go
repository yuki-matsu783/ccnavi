package approval

import (
	"strings"
)

// テストの道具。チケットの見本を作る（git のワークスペースと置き場は testkit）。

const phasesCopy = `phases:
  research:
    kind: work
    title: 調査
    review: false
    scope:
    - wip/research/*
  design:
    kind: work
    title: 設計
    review: true
    scope:
    - wip/design/*
`

// parentText は親の見本。plan は計画の行（"  - research" の形）。
func parentText(id string, plan ...string) string {
	return "---\nversion: 1\nticket: " + id + "\nmr: 1\nissue: 1\n" + phasesCopy +
		"plan:\n" + strings.Join(plan, "\n") + "\n" +
		"human_review:\n  required: true\n  reason: t\ntitle: 親\nrationale: r\n" +
		"allow:\n  - match: Write|Edit\n    glob: \"src/*\"\n  - match: Write|Edit\n    glob: \"wip/*\"\n" +
		"started_at: \"\"\ncompleted_at: \"\"\nbase_sha: \"\"\n---\n\n本文\n"
}

// childText は子の見本。extra は frontmatter に足す行。
func childText(id, parent string, phase string, glob string, extra ...string) string {
	lines := ""
	for _, e := range extra {
		lines += e + "\n"
	}
	return "---\nversion: 1\nticket: " + id + "\nparent: " + parent + "\nphase: " + phase + "\n" +
		"human_review:\n  required: true\n  reason: t\ntitle: 子 " + id + "\nrationale: r\n" +
		"allow:\n  - match: Write|Edit\n    glob: \"" + glob + "\"\n" + lines + "---\n\n本文\n"
}
