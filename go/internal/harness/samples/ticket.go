package samples

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
)

// Rules は見本のルール（JSON。共通レイヤーの rules.yml に書く）。
// 守る場所のコピーを置く wip/handoff/ の中は、本物のルールと同じく除く。
const Rules = `{"version": 1, "deny": [{"id": "guard-approved", "match": "Write|Edit|NotebookEdit", "glob": "*/.ccnavi/*", ` +
	`"except": [{"regex": "[\\\\/]wip[\\\\/]handoff[\\\\/]"}], ` +
	`"message": "ガードの設定と承認済みチケットです。ユーザに依頼してください。"}]}`

// Phases は見本のフェーズ定義（phases.yml）。
const Phases = `
version: 1
phases:
  research:
    kind: work
    title: 調査
    review: false
    scope: ["wip/research/*"]
    deliverables: ["wip/research/summary.md"]
    agent: explorer
    skill: process-research
    when: 既存の振る舞いが分からないとき
  design:
    kind: work
    title: 設計
    review: true
    scope: ["wip/design/*"]
  acceptance:
    kind: work
    title: 受入テスト作成
    review: true
    scope: ["tests/*"]
  implement:
    kind: work
    title: 実装とテスト
    review: true
    scope: ["src/*", "tests/*"]
  implement-feedback:
    kind: feedback
    title: 実装フィードバック対応
    review: true
    scope: inherit
  chores:
    kind: work
    title: 片付け
    review: true
    scope: ["src/*"]
  chores-feedback:
    kind: feedback
    title: 片付けフィードバック対応
    review: true
    scope: inherit
`

// PlainPhases は計画の中身を問わないテストの親が持つ、最小の定義。
// plan を持たない親は ticket start が止まるので、そうしたテストの親は work のステップを並べた最小の計画を持つ。
const PlainPhases = `
version: 1
phases:
  work:
    kind: work
    title: 作業
    review: false
    scope: inherit
  work-mr:
    kind: work
    title: MR作業
    review: true
    scope: inherit
`

// Flag は欄を書かない・true・false の 3 つ。
type Flag int

const (
	// Unset は欄を書かない（子の human_review では「宣言なし」）。
	Unset Flag = iota
	On
	Off
)

// Step は計画のステップ 1 つ。After が nil なら、すぐ前のステップが先行する（一直線。最初のステップは先行なし）。
// 並行にしたい見本は After に先行の番号（1 始まり）を明記する。空で nil でない列は「先行なし」。
type Step struct {
	Type     string
	Review   Flag
	Deferred bool
	After    []int
}

// Plan は定義の名前だけで書いたステップの並び。
func Plan(names ...string) []Step {
	steps := make([]Step, len(names))
	for i, n := range names {
		steps[i] = Step{Type: n}
	}
	return steps
}

// ReviewStep は review: true を書くステップ。
func ReviewStep(name string) Step { return Step{Type: name, Review: On} }

// DeferStep は deferred: true を書くステップ。
func DeferStep(name string) Step { return Step{Type: name, Deferred: true} }

// PlanItemLines は計画のステップを YAML の行にする。start はその計画の最初の番号（フィードバック計画は続き）。
func PlanItemLines(items []Step, start int) []string {
	var lines []string
	for i, item := range items {
		after := item.After
		if after == nil {
			after = []int{}
			if i > 0 {
				after = []int{start + i - 1}
			}
		}
		fields := []string{"type: " + item.Type}
		switch item.Review {
		case On:
			fields = append(fields, "review: true")
		case Off:
			fields = append(fields, "review: false")
		}
		if item.Deferred {
			fields = append(fields, "deferred: true")
		}
		if len(after) > 0 {
			nums := make([]string, len(after))
			for k, n := range after {
				nums[k] = fmt.Sprint(n)
			}
			fields = append(fields, "after: ["+strings.Join(nums, ", ")+"]")
		}
		if len(fields) == 1 {
			lines = append(lines, "  - "+item.Type)
		} else {
			lines = append(lines, "  - {"+strings.Join(fields, ", ")+"}")
		}
	}
	return lines
}

// ParentOpts は ParentText の任意の指定。零値は「既定」。
type ParentOpts struct {
	// Feedback が nil でなければ feedback を書く。空なら `feedback: []`。
	Feedback []Step
	// Allow が nil なら src/*・wip/*・tests/*。空で nil でない列は allow を空にする。
	Allow []string
	// Issue が 0 なら 1。
	Issue int
	// Copy は計画が使う定義のコピー元の phases.yml。空なら Phases。
	Copy string
	// NoCopy を立てると phases: を書かない。
	NoCopy bool
}

var defaultAllow = []string{"src/*", "wip/*", "tests/*"}

// ParentText は親の提案の本文。計画を持つ親は、計画が使う定義のコピー（phases:）を持つ。
// phases.yml を書き換えるテストは、同じ本文を ParentOpts.Copy に渡す。
func ParentText(name string, plan []Step, o ParentOpts) string {
	allow := o.Allow
	if allow == nil {
		allow = defaultAllow
	}
	issue := o.Issue
	if issue == 0 {
		issue = 1
	}
	lines := []string{"---", "version: 1", "ticket: " + name, "mr: 1", fmt.Sprintf("issue: %d", issue), "plan:"}
	lines = append(lines, PlanItemLines(plan, 1)...)
	if o.Feedback != nil {
		if len(o.Feedback) > 0 {
			lines = append(lines, "feedback:")
			lines = append(lines, PlanItemLines(o.Feedback, len(plan)+1)...)
		} else {
			lines = append(lines, "feedback: []")
		}
	}
	lines = append(lines, "human_review:", "  required: true", "  reason: t", "title: 親", "rationale: r", "allow:")
	lines = append(lines, allowLines(allow)...)
	lines = append(lines, `started_at: ""`, `completed_at: ""`, `base_sha: ""`, "---", "")
	// 親の必須の見出し（形式検査が見る）。中身は短い一言にして、目印は残さない。
	for _, heading := range []string{"目的", "範囲", "対象外", "完了の確かめ方", "計画の理由"} {
		lines = append(lines, "## "+heading, "", "本文", "")
	}
	text := strings.Join(lines, "\n")
	if o.NoCopy {
		return text
	}
	copyFrom := o.Copy
	if copyFrom == "" {
		copyFrom = Phases
	}
	return InsertPhases(text, copyFrom)
}

func allowLines(globs []string) []string {
	var lines []string
	for _, g := range globs {
		lines = append(lines, "  - match: Write|Edit", `    glob: "`+g+`"`)
	}
	return lines
}

// ChildText は子の提案の本文（human_review: required: true）。
func ChildText(name, parent string, phase int, allow []string) string {
	return ChildTextReview(name, parent, phase, allow, On)
}

// ChildTextReview は review が Unset なら human_review を書かない（宣言なし）。
func ChildTextReview(name, parent string, phase int, allow []string, review Flag) string {
	lines := []string{"---", "version: 1", "ticket: " + name, "parent: " + parent, fmt.Sprintf("phase: %d", phase)}
	if review != Unset {
		lines = append(lines, "human_review:", "  required: "+map[Flag]string{On: "true", Off: "false"}[review], "  reason: t")
	}
	lines = append(lines, "title: 子 "+name, "rationale: r", "allow:")
	lines = append(lines, allowLines(allow)...)
	lines = append(lines, `started_at: ""`, `completed_at: ""`, `base_sha: ""`, "---", "", "本文")
	return strings.Join(lines, "\n") + "\n"
}

// Work は定義 work のステップ n 個の名前の並び（PlainParent に渡す）。
func Work(n int) []string {
	names := make([]string, n)
	for i := range names {
		names[i] = "work"
	}
	return names
}

// PlainPlanLines は plan: の行。最後のステップだけに前の全部が先行する
// （終端は 1 つにしないと承認が通らない）。
func PlainPlanLines(names []string) []string {
	last := len(names)
	before := make([]string, 0, last)
	for k := 1; k < last; k++ {
		before = append(before, fmt.Sprint(k))
	}
	lines := []string{"plan:"}
	for i, n := range names {
		after := ""
		if i+1 == last {
			after = strings.Join(before, ", ")
		}
		lines = append(lines, fmt.Sprintf("  - {type: %s, after: [%s]}", n, after))
	}
	return lines
}

// PlainParent は計画を持たない親の本文に、最小の計画（plan の名前の並び）とコピーを足す。
// plan が空か、子（parent: を持つ）か、すでに plan: がある本文は、そのまま返す。
func PlainParent(text string, plan []string) string {
	if len(plan) == 0 || strings.Contains(text, "\nparent:") || strings.Contains(text, "\nplan:") {
		return text
	}
	lines := strings.Split(text, "\n")
	at := -1
	for i, line := range lines {
		if strings.HasPrefix(line, "human_review:") {
			at = i
			break
		}
	}
	if at < 0 {
		panic("samples.PlainParent: human_review: の行が無い")
	}
	out := append([]string{}, lines[:at]...)
	out = append(out, PlainPlanLines(plan)...)
	out = append(out, lines[at:]...)
	return InsertPhases(strings.Join(out, "\n"), PlainPhases)
}

// WritePlainPhases は PlainPhases をそのルートの config の phases.yml に書く（親の承認がコピーを確かめる）。
func WritePlainPhases(t testing.TB, root string) string {
	t.Helper()
	path := base.ConfigPath(root, "phases")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(PlainPhases), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}
