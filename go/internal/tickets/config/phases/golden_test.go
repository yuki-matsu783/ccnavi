package phases

import (
	"encoding/json"
	"reflect"
	"strconv"
	"strings"
	"testing"

	"ccnavi/internal/infra/golden"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/model/ticket"
)

// 見本と突き合わせる。対象は定義の parse・merge・コピーの検査・範囲と、先行関係・計画の検査。
// 定義の読みと苦情の文はレビューの要否と範囲の上限を決めるので、欄ごとの誤りを並べた入力で 1 字ずつ見る。

type typeRow []any

type defRow struct {
	Problems [][]string `json:"problems"`
	Types    []typeRow  `json:"types"`
	Diff     [][]any    `json:"diff,omitempty"`
	// CopyProblems・Copied は同じ定義を親の phases: として読んだもの（誤りのある定義を落とし、ほかは残す）。
	CopyProblems [][]string `json:"copyProblems,omitempty"`
	Copied       []typeRow  `json:"copied,omitempty"`
}

type ticketRow struct {
	Copy         [][]string       `json:"copy"`
	Types        []typeRow        `json:"types"`
	Plan         [][]string       `json:"plan"`
	PlanOnly     [][]string       `json:"planOnly"`
	FeedbackOnly [][]string       `json:"feedbackOnly"`
	Waits        map[string][]int `json:"waits"`
	ReviewAt     map[string]int   `json:"reviewAt"`
	Lines        []string         `json:"lines"`
	Ready        []int            `json:"ready"`
	Loose        []int            `json:"loose"`
	Childless    [][]string       `json:"childless"`
	Errors       [][]string       `json:"errors"`
	Terminal     [][]string       `json:"terminal"`
	After        [][]string       `json:"after"`
	WaitsOf      [][]int          `json:"waitsOf"`
	ParentScope  [][]string       `json:"parentScope"`
	Scope        [][][]string     `json:"scope"`
	ChildCopy    [][]string       `json:"childCopy"`
}

type phasesAnswers struct {
	Defs    []defRow    `json:"defs"`
	Merges  []defRow    `json:"merges"`
	Tickets []ticketRow `json:"tickets"`
}

const samplePhases = `version: 1
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
  design: {kind: work, title: 設計, review: true, scope: ["wip/design/*"]}
  implement: {kind: work, title: 実装とテスト, scope: ["src/*", "tests/*"]}
  implement-feedback: {kind: feedback, title: 実装フィードバック対応, scope: inherit}
  quick-work: {title: 軽い作業, scope: inherit, review: false}
`

func defs(lines ...string) string {
	return "version: 1\nphases:\n" + strings.Join(lines, "\n") + "\n"
}

var defCases = []string{
	samplePhases,
	"version: 2\nphases: {a: {}}\n",
	"version: true\nphases: {a: {}}\n",
	"version: 1.0\nphases: {a: {}}\n",
	"version: '1'\nphases: {a: {}}\n",
	"phases: {a: {}}\n",
	"version: 1\n",
	"version: 1\nphases: {}\n",
	"version: 1\nphases: [a]\n",
	"- 1\n",
	"version: 1\nphases: {a: [}\n",
	"",
	defs("  a:b: {}", "  -x: {}", "  ' ok ': {}", "  3: {}", "  ok: {title: 2}", "  ガ: {}"),
	defs("  a: 3", "  b: {kind: chore}", "  c: {review: mr}", "  d: {review: null}", "  e: {kind: feedback, review: false}"),
	defs("  a: {scope: src/*}", "  b: {scope: [src/*, 3]}", "  c: {scope: ['  ']}", "  d: {scope: ['src/a b']}",
		"  e: {scope: ['../x']}", "  f: {scope: ['~/x']}", "  g: {scope: ['$HOME/x']}", "  h: {scope: ['/abs']}"),
	defs("  a: {scope: ['src\\a\\*', '/']}", "  b: {scope: [' src/ ', '//docs//']}", "  c: {scope: []}", "  d: {scope: null}",
		"  e: {scope: inherit}", "  f: {scope: Inherit}", "  g: {scope: ['[a-', 'x[!b]y', 'SRC/**']}"),
	defs("  a: {scope: ["+strings.Repeat("x, ", 21)+"]}", "  b: {deliverables: ["+strings.Repeat("x, ", 21)+"]}",
		"  c: {scope: ["+strings.Repeat("x, ", 20)+"]}"),
	defs("  a: {deliverables: x}", "  b: {deliverables: [x, 1]}", "  c: {deliverables: [' a ', '', '  ']}",
		"  d: {deliverables: ['../x']}", "  e: {deliverables: ['/x']}", "  f: {deliverables: null}"),
	defs("  a: {learning: true}", "  b: {learning: false}", "  c: {learning: ' OFF '}", "  d: {learning: maybe}",
		"  e: {learning: 1}", "  f: {learning: null}", "  g: {learning: 'on'}"),
	defs("  a: {title: 同じ}", "  b: {title: 同じ}", "  c: {title: a}", "  a2: {}", "  d: {title: ' '}"),
	defs("  a: {scope: ['src\\a\\*', ' src/ ', 'Docs//X/']}", "  b: {scope: ['a/../b']}"),
	defs("  a: {agent: 3, skill: 0, when: ' x ', title: [1]}", "  b: {kind: ' feedback ', review: true}", "  c: {kind: 0}"),
}

var mergeCases = [][2]string{
	{samplePhases, samplePhases},
	{samplePhases, defs("  research: {kind: work, title: 調査, review: false, scope: ['WIP/research/*']}",
		"  design: {kind: work, title: 設計, review: true, scope: ['wip/design/*'], learning: off}",
		"  mine: {title: 調査}", "  own: {title: 自分の}", "  own2: {title: 自分の}")},
	{samplePhases, defs("  research: {kind: work, title: 調査, review: false, scope: [wip/research/*], deliverables: [wip/research/summary.md], agent: explorer, skill: process-research, when: 既存の振る舞いが分からないとき}")},
	{"version: 9\n", samplePhases},
}

var probePaths = []string{"wip/research/a.md", "WIP/Research/A.md", "wip/design", "src/x.py", "tests/a/b.py", "docs/x.md", ""}

const copyBlock = `phases:
  research: {kind: work, title: 調査, review: false, scope: ["wip/research/*"]}
  design: {kind: work, title: 設計, review: true, scope: ["wip/design/*"]}
  implement: {kind: work, title: 実装とテスト, scope: ["src/*", "tests/*"]}
  implement-feedback: {kind: feedback, title: 実装フィードバック対応, scope: inherit}`

func parentText(lines ...string) string {
	front := []string{"---", "version: 1", "ticket: i0001", "mr: 1", "issue: 1"}
	front = append(front, lines...)
	front = append(front, "title: 親", "rationale: r", "allow:", "  - match: Write|Edit", `    glob: "src/*"`,
		"  - match: Write|Edit", `    glob: "wip/*"`, "  - match: Write", `    glob: "*.md"`,
		"  - match: Write", `    glob: "docs/**"`, "  - match: Write", `    regex: "^x"`,
		"deny:", "  - match: Write", `    glob: "secret/*"`, "---", "", "本文", "")
	return strings.Join(front, "\n")
}

const childText = `---
version: 1
ticket: i0001-03-01
parent: i0001
phase: 3
title: 子
allow:
  - match: Write|Edit
    glob: "src/*"
  - match: Write
    glob: "src/a/b.py"
  - match: Write
    glob: "docs/*"
  - match: Write
    regex: "^src/"
deny:
  - match: Write
    glob: "anywhere/*"
phases:
  x: {}
---
`

type ticketCase struct {
	Parent      string `json:"parent"`
	Child       string `json:"child"`
	Project     string `json:"project"`
	ChildPhases []int  `json:"childPhases"`
}

var ticketCases = []ticketCase{
	{Parent: parentText(copyBlock, "plan:", "  - research", "  - {type: design, after: [1]}", "  - {type: implement, after: [2]}",
		"feedback:", "  - implement-feedback"), Child: childText, ChildPhases: []int{1}},
	// 並行の枝と合流、延期
	{Parent: parentText(copyBlock, "plan:", "  - research", "  - {type: design, after: []}", "  - {type: implement, deferred: true, after: [1]}",
		"  - {type: implement, after: [2, 3]}", "feedback:", "  - implement-feedback", "  - {type: implement-feedback, after: [5]}"),
		Project: "web", ChildPhases: []int{2}},
	// 終端が 2 つ、延期の引き受け手が無い、最後の延期、引き受け手にレビューが無い
	{Parent: parentText(copyBlock, "plan:", "  - research", "  - {type: research, deferred: true, after: [1]}", "  - {type: research, after: [1]}",
		"  - {type: design, deferred: true}", "feedback:", "  - implement-feedback", "  - {type: implement-feedback}", "  - {type: implement-feedback, deferred: true, after: [5, 6, 1, 9, x]}")},
	{Parent: parentText(copyBlock, "plan:", "  - {type: design, deferred: true}", "  - {type: research, after: [1], review: false}",
		"  - {type: implement, after: [2, 2, 1]}")},
	// コピーが無い、定義がコピーに無い、kind の食い違い、使わない定義、フィードバックの review: false
	{Parent: parentText("plan:", "  - research")},
	{Parent: parentText(copyBlock, "  bad:b: {}", "  broken: {kind: x}", "plan:", "  - research", "  - {type: implement-feedback, after: [1]}", "  - {type: nowhere, after: [2]}",
		"  - {type: broken, after: [3]}", "feedback:", "  - {type: design, review: false}")},
	{Parent: parentText("phases: [x]", "plan:", "  - research")},
	{Parent: parentText("phases:", "  research: {kind: work, title: 調査, scope: inherit}", "plan:", "  - research"), Child: childText},
	{Parent: parentText("phases:", "  research: {kind: work, title: 調査, scope: [tests/*]}", "plan:", "  - research"), Child: childText},
	{Parent: parentText()},
	{Parent: parentText(copyBlock)},
	{Parent: parentText(copyBlock, "plan:", "  - research", "feedback: []")},
}

func probs(ps []rules.Problem) [][]string {
	out := [][]string{}
	for _, p := range ps {
		out = append(out, []string{string(p.Severity), p.Rule, p.Detail})
	}
	return out
}

func jsonOf(v any) any {
	switch x := v.(type) {
	case *yaml.Map:
		o := pyjson.NewObject()
		for k, item := range x.All() {
			o.Set(k.(string), jsonOf(item))
		}
		return o
	case []any:
		out := make([]any, len(x))
		for i, item := range x {
			out[i] = jsonOf(item)
		}
		return out
	}
	return v
}

func show(t *testing.T, types *Types) []typeRow {
	if types == nil {
		return nil
	}
	out := []typeRow{}
	for p := range types.All() {
		text, err := pyjson.Dumps(jsonOf(p.AsCopy()), pyjson.Options{RawUnicode: true})
		if err != nil {
			t.Fatal(err)
		}
		var scope any
		if !p.Inherit {
			scope = orEmpty(p.ScopeGlobs)
		}
		decided := []any{}
		for _, path := range probePaths {
			decided = append(decided, string(p.Decide(path)))
		}
		out = append(out, typeRow{p.ID, p.Title, string(p.Kind), p.Review, scope, orEmpty(p.Deliverables),
			p.Agent, p.Skill, p.When, string(p.Learning), p.Source, text, decided})
	}
	return out
}

func orEmpty(list []string) []string {
	if list == nil {
		return []string{}
	}
	return list
}

func ints(list []int) []int {
	if list == nil {
		return []int{}
	}
	return list
}

// same は JSON に通した形で比べる（Go の nil と空の列・any の数の型の違いを消す）。
func same(t *testing.T, label string, got, want any) {
	t.Helper()
	g, _ := json.Marshal(got)
	w, _ := json.Marshal(want)
	var gv, wv any
	_ = json.Unmarshal(g, &gv)
	_ = json.Unmarshal(w, &wv)
	if !reflect.DeepEqual(gv, wv) {
		t.Errorf("%s:\n got %s\nwant %s", label, g, w)
	}
}

func TestPhasesMatchGolden(t *testing.T) {
	want := golden.Load[phasesAnswers](t, "phases")
	for i, text := range defCases {
		types, problems := Parse(text, where)
		var raw any
		if data, err := yaml.Load(text); err == nil {
			if m, ok := data.(*yaml.Map); ok {
				raw = m.Get("phases")
			}
		}
		copied, copyProblems := ReadCopy(raw)
		same(t, "def "+strconv.Itoa(i), defRow{Problems: probs(problems), Types: show(t, types),
			CopyProblems: probs(copyProblems), Copied: show(t, copied)}, want.Defs[i])
	}
	for i, pair := range mergeCases {
		common, _ := Parse(pair[0], where)
		common.markSource("common")
		extra, _ := Parse(pair[1], where)
		merged, problems := merge(common, extra, "web")
		var diff [][]any
		for p := range extra.All() {
			if prior := common.Get(p.ID); prior != nil {
				diff = append(diff, []any{p.ID, Same(prior, p), orEmpty(Differing(prior, p))})
			}
		}
		same(t, "merge "+strconv.Itoa(i), defRow{Problems: probs(problems), Types: show(t, merged), Diff: diff}, want.Merges[i])
	}
	for i, c := range ticketCases {
		parent, problems := ticket.Parse(c.Parent)
		if parent == nil {
			t.Fatal(problems)
		}
		parent.Project = c.Project
		types := TypesOf(parent)
		wf := Compute(parent)
		waits, reviewAt := map[string][]int{}, map[string]int{}
		for k, v := range wf.Waits {
			waits[strconv.Itoa(k)] = ints(v)
		}
		for k, v := range wf.ReviewAt {
			reviewAt[strconv.Itoa(k)] = v
		}
		var waitsOf [][]int
		for n := range 9 {
			waitsOf = append(waitsOf, ints(WaitsOf(parent, n)))
		}
		got := ticketRow{
			Copy: probs(CopyProblems(parent)), Types: show(t, types),
			Plan: probs(PlanProblems(parent, types, "")), PlanOnly: probs(PlanProblems(parent, types, Plan)),
			FeedbackOnly: probs(PlanProblems(parent, nil, FeedbackPart)),
			Waits:        waits, ReviewAt: reviewAt, Lines: orEmpty(Lines(parent, wf)),
			Ready: ints(Ready(parent, nil)), Loose: ints(Loose(parent, nil)),
			Childless: probs(ChildlessReady(parent, c.ChildPhases)), Errors: probs(Errors(parent)),
			Terminal: probs(TerminalProblems(parent, nil, "")), After: probs(AfterProblems(parent, "")),
			WaitsOf: waitsOf, ParentScope: probs(ParentScopeProblems(parent)),
			Scope: [][][]string{}, ChildCopy: [][]string{},
		}
		if c.Child != "" {
			child, problems := ticket.Parse(c.Child)
			if child == nil {
				t.Fatal(problems)
			}
			for p := range types.All() {
				got.Scope = append(got.Scope, probs(ScopeProblems(child, p)))
			}
			got.ChildCopy = probs(CopyProblems(child))
		}
		same(t, "ticket "+strconv.Itoa(i), got, want.Tickets[i])
	}
}
