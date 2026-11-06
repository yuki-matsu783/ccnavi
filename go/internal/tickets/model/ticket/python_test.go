package ticket

import (
	"encoding/json"
	"fmt"
	"reflect"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/model/pyoracle"
)

// Python の ticket.parse・subset_problems・decide・branch_name_problems・donecopy.copy_fields と突き合わせる。
// 読める・読めないの境界と苦情の文が 1 字も違わないことを、欄ごとの誤りを並べた入力で見る。

const parseOracle = `
import json, sys
from ccnavi.infra import settings
from ccnavi.tickets.model import ticket as tm, ticket_ids, donecopy
def plan(items):
    if items is None:
        return None
    return [{"raw": i.as_raw(), "errors": [[repr(v), w] for v, w in i.after_errors], "repeated": i.after_repeated} for i in items]
def need(v):
    return "" if v is None else ("yes" if v else "no")
out = []
c = json.load(sys.stdin)
parent, _ = tm.parse(c["parent"])
for text in c["texts"]:
    t, problems = tm.parse(text)
    row = {"problems": [[p.severity, p.rule, p.detail] for p in problems], "copy": None}
    cp = donecopy.copy_fields(text)
    if cp is not None:
        row["copy"] = [cp.ticket, cp.parent]
    if t is not None:
        row["ticket"] = {
            "id": t.ticket, "parent": t.parent, "phase": t.phase if t.phase is not None else 0,
            "preds": t.predecessors, "issue": t.issue or 0, "repo": t.issue_repo, "mr": t.mr or 0,
            "project": t.project, "plan": plan(t.plan), "feedback": plan(t.feedback),
            "phases": repr(t.phases_raw), "review": need(t.review_required), "reason": t.review_reason,
            "title": t.title, "rationale": t.rationale, "body": t.body,
            "entries": [[e.decision, e.glob, e.regex] for e in t.entries],
            "script": [getattr(t, k) for k in tm.ticket_model.SCRIPT_FIELDS],
            "maint": repr(t.maintenance), "hasMaint": t.has_maintenance, "maintProblem": t.maintenance_problem(),
            "consolidate": t.is_consolidate,
            "decide": [t.decide(p) for p in c["paths"]],
            "subset": [[p.severity, p.rule, p.detail] for p in tm.subset_problems(t, parent)],
            "names": ticket_ids.branch_name_problems(t, "Trunk", 0, settings.DEFAULT_BRANCH_PREFIXES),
            "issueText": tm.issue_text(t.raw.get("issue")) if "issue" in t.raw else "",
        }
    out.append(row)
json.dump(out, sys.stdout, ensure_ascii=False)
`

type pyTicket struct {
	ID           string     `json:"id"`
	Parent       string     `json:"parent"`
	Phase        int        `json:"phase"`
	Preds        []string   `json:"preds"`
	Issue        int        `json:"issue"`
	Repo         string     `json:"repo"`
	MR           int        `json:"mr"`
	Project      string     `json:"project"`
	Plan         []pyItem   `json:"plan"`
	Feedback     []pyItem   `json:"feedback"`
	Phases       string     `json:"phases"`
	Review       string     `json:"review"`
	Reason       string     `json:"reason"`
	Title        string     `json:"title"`
	Rationale    string     `json:"rationale"`
	Body         string     `json:"body"`
	Entries      [][]string `json:"entries"`
	Script       []string   `json:"script"`
	Maint        string     `json:"maint"`
	HasMaint     bool       `json:"hasMaint"`
	MaintProblem string     `json:"maintProblem"`
	Consolidate  bool       `json:"consolidate"`
	Decide       []string   `json:"decide"`
	Subset       [][]string `json:"subset"`
	Names        []string   `json:"names"`
	IssueText    string     `json:"issueText"`
}

type pyItem struct {
	Raw      any        `json:"raw"`
	Errors   [][]string `json:"errors"`
	Repeated []int      `json:"repeated"`
}

type pyRow struct {
	Problems [][]string `json:"problems"`
	Copy     []string   `json:"copy"`
	Ticket   *pyTicket  `json:"ticket"`
}

const parentText = `---
version: 1
ticket: feature-1-web
title: 親
allow:
  - match: Write|Edit
    glob: "src/*"
  - match: Write
    glob: "docs"
ask:
  - match: Edit
    regex: "^tests/.*"
---
`

// ticketText は frontmatter の行を並べたチケット。scope を空にすれば範囲を書かない。
func ticketText(lines ...string) string {
	return "---\n" + strings.Join(lines, "\n") + "\n---\n本文\n"
}

var scope = []string{"allow:", "  - match: Write|Edit", `    glob: "src/a/*"`}

func with(lines ...string) string {
	return ticketText(append(append([]string{"version: 1"}, lines...), scope...)...)
}

var parseCases = []string{
	"no frontmatter\n",
	"\uFEFF---\nticket: a\n---\n",
	"---\nticket: a\n",
	"---\n---\n",
	"---\n- a\n---\n",
	"---\r\nversion: 1\r\nticket: feature-2-crlf\r\nallow:\r\n  - match: Write\r\n    glob: x\r\n---\r\n本文\r\n2 行目",
	with(),
	with("ticket: \"\""),
	ticketText("version: 2", "ticket: a"),
	ticketText("version: true", "ticket: feature-3-x", "allow:", "  - {match: Write, glob: a}"),
	ticketText("version: 1.0", "ticket: feature-3-x", "allow:", "  - {match: Write, glob: a}"),
	with("ticket: \"a b\""),
	with("ticket: " + strings.Repeat("a", 65)),
	with("ticket: \"ガ\""),
	with("ticket: 123"),
	with("ticket: feature-4-x", "parent: feature-4-x"),
	with("ticket: feature-4-x-01-01", "parent: other"),
	with("ticket: feature-4-x-01-01", "parent: feature-4-x"),
	with("ticket: feature-4-x-01-01", "parent: feature-4-x", "phase: -1"),
	with("ticket: feature-4-x-01-01", "parent: feature-4-x", "phase: true"),
	with("ticket: feature-4-x-01-01", "parent: feature-4-x", "phase: 100"),
	with("ticket: feature-4-x-01-01", "parent: feature-4-x", "phase: 99999999999999999999999"),
	with("ticket: feature-4-x-01-01", "parent: feature-4-x", "phase: 2"),
	with("ticket: feature-4-x-02-01", "parent: feature-4-x", "phase: 2", "predecessors: [a, '', 3]",
		"plan: [x]", "feedback: [y]", "issue: 3", "mr: 4", "project: web"),
	with("ticket: feature-4-x-00-01", "parent: feature-4-x", "phase: 0", "maintenance: learning-consolidate"),
	with("ticket: feature-4-x-01-01", "parent: feature-4-x", "phase: 1", "maintenance: learning-consolidate"),
	with("ticket: feature-4-x-00-01", "parent: feature-4-x", "phase: 0", "maintenance: other"),
	with("ticket: feature-4-x-00-01", "parent: feature-4-x", "phase: 0", "maintenance:"),
	with("ticket: feature-4-x", "maintenance: 3"),
	with("ticket: feature-5-x", "phase: 1", "predecessors: [a]"),
	with("ticket: feature-5-x", "predecessors: a"),
	with("ticket: feature-5-x", "plan: x"),
	with("ticket: feature-5-x", "plan: [a, '  b ', {type: c, review: false}, {type: d, deferred: true, after: [1, 1, 5, 0, true, x]}]"),
	with("ticket: feature-5-x", "plan: [{review: true}]"),
	with("ticket: feature-5-x", "plan: [{type: a, review: 1}]"),
	with("ticket: feature-5-x", "plan: [{type: a, deferred: false}]"),
	with("ticket: feature-5-x", "plan: [{type: a, review: true, deferred: true}]"),
	with("ticket: feature-5-x", "plan: ['']"),
	with("ticket: feature-5-x", "plan: [a, b]", "feedback: [{type: c, after: [1, 3]}, {type: d, after: 3}]"),
	with("ticket: feature-5-x", "plan: [a]", "feedback: []"),
	with("ticket: feature-5-x", "plan: [a, {type: b, after: [99999999999999999999]}]"),
	with("ticket: feature-5-x", "phases: {a: 1}"),
	with("ticket: feature-6-x", "issue: owner/repo#6"),
	with("ticket: feature-6-x", "issue: '#6'"),
	with("ticket: feature-6-x", "issue: 'g/sub/p#7'"),
	with("ticket: feature-6-x", "issue: 'a/../b#7'"),
	with("ticket: feature-6-x", "issue: 0"),
	with("ticket: feature-6-x", "issue: true"),
	with("ticket: feature-6-x", "issue: '１２'"),
	with("ticket: feature-6-x", "mr: '!12'"),
	with("ticket: feature-6-x", "mr: '#12'"),
	with("ticket: feature-6-x", "mr: ''"),
	with("ticket: feature-6-x", "mr: owner/repo!3"),
	with("ticket: feature-6-x", "branch: x"),
	with("ticket: feature-7-x", "human_review: {required: false}"),
	with("ticket: feature-7-x", "human_review: {required: false, reason: 小さい}"),
	with("ticket: feature-7-x", "human_review: {required: yes please}"),
	with("ticket: feature-7-x", "human_review: {reason: x}"),
	with("ticket: feature-7-x", "human_review: false"),
	with("ticket: feature-7-x", "human_review: 3"),
	with("ticket: feature-8-x", "tools: [a]", "deny_commands: []", "target_directories: []",
		"started_at: 2026-01-01", "base_sha: ' abc '", "cancel_reason: 3"),
	ticketText("version: 1", "ticket: feature-9-x"),
	ticketText("version: 1", "ticket: feature-9-x", "allow: x"),
	ticketText("version: 1", "ticket: feature-9-x", "allow:", "  - x", "  - {match: Bash, glob: a}",
		"  - {match: Write}", "  - {match: Write, glob: a, regex: b}", "  - {match: Write, glob: '../a'}",
		"  - {match: Write, glob: '~/a'}", "  - {match: Write, glob: '$HOME'}", "  - {match: Write, glob: 'a b'}",
		"  - {match: Write, glob: '/abs'}", "  - {match: Write, glob: 'C:x'}", "  - {match: Write, regex: '('}",
		"  - {match: ' Edit |Bash', glob: '\\\\src\\\\B\\\\'}", "deny:", "  - {match: Write, glob: 'src/b/secret*'}",
		"ask:", "  - {match: Write, glob: 'docs/*'}"),
	ticketText(append([]string{"version: 1", "ticket: feature-9-x", "allow:"},
		strings.Split(strings.Repeat("  - {match: Write, glob: a}\n", 21), "\n")...)...),
	with("ticket: main"),
	with("ticket: Release-2"),
	with("ticket: trunk"),
	with("ticket: x..y.lock"),
	with("ticket: feature-10-x."),
	with("ticket: feature-10-web-x", "issue: 11", "project: web"),
	with("ticket: feature-11-x", "issue: 11", "project: web"),
	with("ticket: feature-11-web", "issue: 11", "project: web"),
	with("ticket: feature-11-y", "issue: o/r#3"),
	with("ticket: other-11-y", "issue: 11"),
	with("ticket: feature-12-" + strings.Repeat("y", 40)),
}

var decidePaths = []string{"src/a/x", "SRC/A/x", "src/a", "src/b/secret.txt", "docs/x", "docs", "tests/x", "other"}

func entriesOf(t *Ticket) [][]string {
	out := [][]string{}
	for _, e := range t.Entries {
		out = append(out, []string{string(e.Decision), e.Glob, e.Regex})
	}
	return out
}

func itemsOf(items []PlanItem, planned bool) []pyItem {
	if !planned {
		return nil
	}
	out := []pyItem{}
	for _, i := range items {
		row := pyItem{Raw: jsonish(i.Raw()), Errors: [][]string{}, Repeated: i.AfterRepeated}
		if row.Repeated == nil {
			row.Repeated = []int{}
		}
		for _, e := range i.AfterErrors {
			row.Errors = append(row.Errors, []string{pyfmt.Repr(e.Value), e.Reason})
		}
		out = append(out, row)
	}
	return out
}

// jsonish は書き戻す形を encoding/json が出せる形にする。
func jsonish(v any) any {
	if o, ok := v.(*pyjson.Object); ok {
		m := map[string]any{}
		for k, x := range o.All() {
			m[fmt.Sprint(k)] = jsonish(x)
		}
		return m
	}
	return v
}

// roundTrip は JSON に出して読み直す（Go と Python の答えを同じ形で比べる）。
func roundTrip(t *testing.T, v any) any {
	t.Helper()
	data, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	var out any
	if err := json.Unmarshal(data, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

func TestParseMatchesPython(t *testing.T) {
	in := map[string]any{"parent": parentText, "texts": parseCases, "paths": decidePaths}
	want := pyoracle.Answers[[]pyRow](t, "parse", parseOracle, in)
	parent, _ := Parse(parentText)
	for i, text := range parseCases {
		got := pyRow{Problems: [][]string{}}
		tk, problems := Parse(text)
		for _, p := range problems {
			got.Problems = append(got.Problems, []string{string(p.Severity), p.Rule, p.Detail})
		}
		if c, ok := CopyFields(text); ok {
			got.Copy = []string{c.Ticket, c.Parent}
		}
		if tk != nil {
			script := []string{}
			for _, k := range ScriptFields {
				script = append(script, *tk.scriptField(k))
			}
			review := map[Need]string{Unsaid: "", Needed: "yes", Unneeded: "no"}[tk.ReviewRequired]
			decide := []string{}
			for _, p := range decidePaths {
				decide = append(decide, string(tk.Decide(p)))
			}
			subset := [][]string{}
			for _, p := range SubsetProblems(tk, parent) {
				subset = append(subset, []string{string(p.Severity), p.Rule, p.Detail})
			}
			names := tk.BranchNameProblems("Trunk", config.DefaultBranchPrefixes)
			if names == nil {
				names = []string{}
			}
			preds := tk.Predecessors
			if preds == nil {
				preds = []string{}
			}
			maint := "None"
			if tk.HasMaintenance {
				maint = pyfmt.Repr(tk.Maintenance)
			}
			issueText := ""
			if tk.Raw.Has("issue") {
				issueText = IssueText(tk.Raw.Get("issue"))
			}
			got.Ticket = &pyTicket{
				ID: tk.ID, Parent: tk.Parent, Phase: tk.Phase, Preds: preds, Issue: tk.Issue, Repo: tk.IssueRepo,
				MR: tk.MR, Project: tk.Project, Plan: itemsOf(tk.Plan, true), Feedback: itemsOf(tk.Feedback, tk.FeedbackPlanned),
				Phases: pyfmt.Repr(tk.PhasesRaw), Review: review, Reason: tk.ReviewReason, Title: tk.Title,
				Rationale: tk.Rationale, Body: tk.Body, Entries: entriesOf(tk), Script: script, Maint: maint,
				HasMaint: tk.HasMaintenance, MaintProblem: tk.MaintenanceProblem(), Consolidate: tk.IsConsolidate(),
				Decide: decide, Subset: subset, Names: names, IssueText: issueText,
			}
		}
		if i >= len(want) {
			t.Fatalf("Python の答えが足りない")
		}
		if !reflect.DeepEqual(roundTrip(t, got), roundTrip(t, want[i])) {
			t.Errorf("case %d %q\n got  %+v\n want %+v", i, text, roundTrip(t, got), roundTrip(t, want[i]))
		}
	}
}

// 壊れた YAML の苦情の文は infra/yaml（純 Python の読み手）の文で、Python の実行ファイル（libyaml）とは違う。
// ここは止まることと前置きだけを見る。
func TestBrokenYAMLStops(t *testing.T) {
	tk, problems := Parse("---\n: [\n---\n")
	if tk != nil || len(problems) != 1 || !strings.HasPrefix(problems[0].Detail, "frontmatter を YAML として読めない (") {
		t.Fatalf("%v %v", tk, problems)
	}
	if _, err := FrontMapping("---\n: [\n---\n"); err == nil {
		t.Fatal("読めない YAML が error にならない")
	}
}

func TestPlanFieldsDefault(t *testing.T) {
	// 計画が無い親の Plan は空で、FeedbackPlanned は偽（Python の feedback is None）。
	tk, _ := Parse(with("ticket: feature-1-x"))
	if tk == nil || tk.HasPlan() || tk.FeedbackPlanned {
		t.Fatalf("%+v", tk)
	}
}

const renderOracle = `
import json, sys
from ccnavi.tickets.model import ticket as tm
out = []
for text in json.load(sys.stdin):
    t, _ = tm.parse(text)
    out.append(None if t is None else tm.render(t))
json.dump(out, sys.stdout, ensure_ascii=False)
`

// 書き戻した文面が Python の ticket.render と 1 字も違わないこと（承認済みチケットのバイト列になる）。
func TestRenderMatchesPython(t *testing.T) {
	cases := append([]string{renderSample}, parseCases...)
	want := pyoracle.Answers[[]*string](t, "render", renderOracle, cases)
	for i, text := range cases {
		tk, _ := Parse(text)
		if tk == nil {
			if want[i] != nil {
				t.Errorf("case %d: Go は読めない", i)
			}
			continue
		}
		got, err := Render(tk)
		if err != nil {
			t.Fatalf("case %d: %v", i, err)
		}
		if want[i] == nil || got != *want[i] {
			t.Errorf("case %d %q\n got  %q\n want %v", i, text, got, want[i])
		}
	}
}

// renderSample は書き出しで形が変わる値（長い文・引用が要る文字列・日付・別名・空の列）を並べたチケット。
const renderSample = "---\nversion: 1\nticket: i0009\nhuman_review: &hr\n  required: false\n  reason: テスト\n" +
	"title: 'yes'\nwhen: 2026-01-02\nrationale: |\n  理由の 1 行目\n  とても長い理由の 2 行目。八十字を超えるように、同じことを何度も書いて、PyYAML が行を折るかを確かめる。\n" +
	"allow:\n  - match: Write|Edit\n    glob: \"src/*\"\n  - {match: Bash, regex: '^git (status|diff)'}\n" +
	"copy: *hr\npredecessors: []\nstarted_at: \"\"\nnote: \"a: b # c\"\n---\n\n本文\n"
