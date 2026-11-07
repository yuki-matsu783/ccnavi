package phase

import (
	"strings"
	"testing"

	"ccnavi/internal/tickets/model/ticket"
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
    when: 設計の案内
    scope:
    - wip/design/*
  implement:
    kind: work
    title: 実装
    review: true
    when: 実装の案内
    scope:
    - src/*
    - wip/design/*
`

// parentText は親の見本。plan は計画の行（"  - research" の形）で、空なら計画も定義のコピーも書かない。
func parentText(id string, plan ...string) string {
	head := "---\nversion: 1\nticket: " + id + "\nmr: 1\nissue: 1\n"
	if len(plan) > 0 {
		head += phasesCopy + "plan:\n" + strings.Join(plan, "\n") + "\n"
	}
	return head + "human_review:\n  required: true\n  reason: t\ntitle: 親\nrationale: r\n" +
		"allow:\n  - match: Write|Edit\n    glob: \"src/*\"\n  - match: Write|Edit\n    glob: \"wip/*\"\n" +
		"started_at: \"\"\ncompleted_at: \"\"\nbase_sha: \"\"\n---\n\n本文\n"
}

// childText は子の見本。required は human_review.required の値（空なら書かない）、extra は frontmatter に足す行。
func childText(id, parent, phase, required string, globs []string, extra ...string) string {
	text := "---\nversion: 1\nticket: " + id + "\nparent: " + parent + "\nphase: " + phase + "\n"
	if required != "" {
		text += "human_review:\n  required: " + required + "\n  reason: t\n"
	}
	text += "title: 子 " + id + "\nrationale: r\nallow:\n"
	for _, g := range globs {
		text += "  - match: Write|Edit\n    glob: \"" + g + "\"\n"
	}
	for _, e := range extra {
		text += e + "\n"
	}
	return text + "---\n\n本文\n"
}

func parsed(t *testing.T, text string) *ticket.Ticket {
	t.Helper()
	found, problems := ticket.Parse(text)
	if found == nil {
		t.Fatalf("読めない: %v", problems)
	}
	return found
}
