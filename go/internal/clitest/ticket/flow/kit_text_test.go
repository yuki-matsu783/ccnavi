// このファイルは T1 の ticketkit（ブランチ p3-t1 の go/internal/harness/ticketkit）の写し。ticketkit を取り込んだら、写しを消して import に置き換える。
// 写しとの違いは、Cached が写しを取らず毎回組むこと（kit_tree_test.go）。

package flow

import (
	"fmt"
	"strings"

	"ccnavi/internal/harness/samples"
)

// TicketOpts は TicketText の指定。零値は「既定」。
type TicketOpts struct {
	// Parent があれば子の提案（Phase を足す）。空なら親の提案（mr: 1 と issue を書き、最小の計画を持つ）。
	Parent string
	Phase  int
	// Allow・Ask は Write|Edit の範囲。
	Allow, Ask []string
	// NoReview を立てると human_review の required は false。
	NoReview     bool
	Predecessors []string
	// Title は既定で「作業」。
	Title string
	// Issue が 0 のとき、親は 1、子は書かない。
	Issue int
	// Plan は親の計画の定義名の並び。nil なら work 1 つ。
	Plan []string
}

// TicketText は提案の本文。親は最小の計画（work の並び）とフェーズ定義のコピーを持つ
// （守りを試すテストが、計画の中身を問わずに親を承認できるようにする）。
func TicketText(name string, o TicketOpts) string {
	lines := []string{"---", "version: 1", "ticket: " + name}
	issue := o.Issue
	if o.Parent == "" {
		lines = append(lines, "mr: 1")
		if issue == 0 {
			issue = 1
		}
	}
	if issue != 0 {
		lines = append(lines, fmt.Sprintf("issue: %d", issue))
	}
	if o.Parent != "" {
		lines = append(lines, "parent: "+o.Parent, fmt.Sprintf("phase: %d", o.Phase))
	}
	if len(o.Predecessors) > 0 {
		lines = append(lines, "predecessors: ["+strings.Join(o.Predecessors, ", ")+"]")
	}
	required := "true"
	if o.NoReview {
		required = "false"
	}
	title := o.Title
	if title == "" {
		title = "作業"
	}
	lines = append(lines, "human_review:", "  required: "+required, "  reason: テスト", "title: "+title, "rationale: |", "  理由")
	for _, section := range []struct {
		name  string
		globs []string
	}{{"allow", o.Allow}, {"ask", o.Ask}} {
		if len(section.globs) == 0 {
			continue
		}
		lines = append(lines, section.name+":")
		for _, g := range section.globs {
			lines = append(lines, "  - match: Write|Edit", `    glob: "`+g+`"`)
		}
	}
	lines = append(lines, `started_at: ""`, `completed_at: ""`, `base_sha: ""`, "---", "", "本文")
	plan := o.Plan
	if plan == nil {
		plan = samples.Work(1)
	}
	return samples.PlainParent(strings.Join(lines, "\n")+"\n", plan)
}
