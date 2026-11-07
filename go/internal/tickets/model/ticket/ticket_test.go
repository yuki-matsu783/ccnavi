package ticket

import (
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/model/place"

	"ccnavi/internal/tickets/testkit"
)

const sample = "---\nversion: 1\nticket: i0001\nhuman_review:\n  required: false\n  reason: テスト\ntitle: 作業\n" +
	"rationale: |\n  理由\nallow:\n  - match: Write|Edit\n    glob: \"src/*\"\nstarted_at: \"\"\n---\n\n本文の 1 行目\n本文の 2 行目\n"

// CRLF のチケットも LF と同じ中身に読み、本文に \r を残さない（機械によって範囲の判定を変えない）。
func TestLoadReadsCRLFTheSame(t *testing.T) {
	dir := t.TempDir()
	lf, crlf := filepath.Join(dir, "lf.md"), filepath.Join(dir, "crlf.md")
	testkit.Put(t, lf, sample)
	testkit.Put(t, crlf, strings.ReplaceAll(sample, "\n", "\r\n"))
	a, _ := Load(lf)
	b, _ := Load(crlf)
	if a == nil || b == nil {
		t.Fatal(a, b)
	}
	if !reflect.DeepEqual(a.Raw, b.Raw) || a.Title != b.Title || strings.Contains(b.Body, "\r") || a.Body != b.Body {
		t.Errorf("%+v\n%+v", a, b)
	}
	if string(b.Source) != strings.ReplaceAll(sample, "\n", "\r\n") || b.Path != crlf {
		t.Error("Source と Path は読んだファイルのまま")
	}
}

func TestLoadMissingIsNotAProblem(t *testing.T) {
	tk, problems := Load(filepath.Join(t.TempDir(), "none.md"))
	if tk != nil || problems != nil {
		t.Fatal(tk, problems)
	}
}

func TestScanAll(t *testing.T) {
	root, _ := filepath.EvalSymlinks(t.TempDir())
	base := filepath.Join(root, "wip", "proposals")
	testkit.Put(t, filepath.Join(base, "todo", "i0001.md"), sample)
	testkit.Put(t, filepath.Join(base, "todo", "i0002.md"), sample)
	testkit.Put(t, filepath.Join(base, "todo", "note.txt"), "x")
	testkit.Put(t, filepath.Join(base, "review", "bad.md"), "x")
	found, problems := Scan(root, "wip/proposals", filepath.Join(root, "projects"), nil)
	if len(found) != 1 || found[0].ID != "i0001" || found[0].State != place.Todo || found[0].TreeRoot != root || found[0].Tree != tree.Main {
		t.Fatalf("%+v", found)
	}
	var details []string
	for _, p := range problems {
		details = append(details, p.Rule+": "+p.Detail)
	}
	want := []string{
		"i0001: ファイル名 i0002.md と識別子 i0001 が一致しない",
		"(file) (" + filepath.Join("wip", "proposals", "review", "bad.md") + "): 先頭が `---` で始まっていない",
	}
	if !reflect.DeepEqual(details, want) {
		t.Errorf("%q", details)
	}
}

func at(id, parent, tree, project string, state place.State) *Ticket {
	return &Ticket{ID: id, Parent: parent, Tree: tree, Project: project, State: state}
}

func places(ts []*Ticket) []string {
	var out []string
	for _, t := range ts {
		out = append(out, t.Tree+":"+string(t.State))
	}
	return out
}

func TestFold(t *testing.T) {
	home := at("p-01-01", "p", "p", "", place.Todo)
	stale := at("p-01-01", "p", "p-01-01", "", place.Todo)
	cases := []struct {
		name           string
		hits, approved []*Ticket
		want           []string
	}{
		{"親のツリーが正", []*Ticket{stale, home}, nil, []string{"p:todo"}},
		{"親のツリーに無ければ全部残す", []*Ticket{stale}, nil, []string{"p-01-01:todo"}},
		{"リポジトリをまたげばまとめない", []*Ticket{stale, at("p-01-01", "p", "p", "web", place.Todo)}, nil, []string{"p-01-01:todo", "p:todo"}},
		{"承認済みのツリーで決める", []*Ticket{stale, home}, []*Ticket{at("p-01-01", "p", "p", "", place.Doing)}, []string{"p:todo"}},
		{"承認済みのツリーに提案が無ければ残らない", []*Ticket{stale}, []*Ticket{at("p-01-01", "p", "p", "", place.Doing)}, nil},
		{"承認済みの正が決まらなければ全部残す", []*Ticket{stale, home}, []*Ticket{at("p-01-01", "p", "x", "", place.Doing)}, []string{"p-01-01:todo", "p:todo"}},
	}
	for _, c := range cases {
		if got := places(Fold(c.hits, c.approved)); !reflect.DeepEqual(got, c.want) {
			t.Errorf("%s: %v", c.name, got)
		}
	}
	if where, ok := Authority([]*Ticket{at("p", "", "x", "", ""), at("p", "", "p", "", "")}); !ok || where != "p" {
		t.Error(where, ok)
	}
	got := Dedupe([]*Ticket{stale, home, at("q", "", "x", "", place.Todo)}, nil)
	if !reflect.DeepEqual(places(got), []string{"p:todo", "x:todo"}) {
		t.Error(places(got))
	}
}

func TestCollisions(t *testing.T) {
	cases := []struct {
		name   string
		hits   []*Ticket
		across bool
		want   int
	}{
		{"同じツリーの 2 つの置き場", []*Ticket{at("p", "", "p", "", place.Doing), at("p", "", "p", "", place.Done)}, false, 2},
		{"todo と承認済みは並んでよい", []*Ticket{at("p", "", "p", "", place.Todo), at("p", "", "p", "", place.Doing)}, false, 0},
		{"同じ置き場に 2 つ", []*Ticket{at("p", "", "p", "", place.Todo), at("p", "", "p", "", place.Todo)}, false, 2},
		{"ほかのツリーのコピーだけ", []*Ticket{at("p", "", "a", "", place.Doing), at("p", "", "b", "", place.Doing)}, true, 2},
		{"閉じたものだけなら数えない", []*Ticket{at("p", "", "a", "", place.Done), at("p", "", "b", "", place.Cancelled)}, true, 0},
		{"ツリーをまたぐのは普通", []*Ticket{at("p", "", "a", "", place.Doing), at("p", "", "b", "", place.Doing)}, false, 0},
	}
	for _, c := range cases {
		if got := len(Collisions(c.hits, c.across)); got != c.want {
			t.Errorf("%s: %d", c.name, got)
		}
	}
}

func TestReviewAt(t *testing.T) {
	tk := &Ticket{Plan: []PlanItem{{Type: "a", Deferred: true}, {Type: "b", Deferred: true}, {Type: "c"}},
		Feedback: []PlanItem{{Type: "d", Deferred: true}}, FeedbackPlanned: true}
	for n, want := range map[int][2]int{1: {3, 1}, 2: {3, 1}, 3: {3, 1}, 4: {0, 0}, 9: {0, 0}} {
		at, ok := tk.ReviewAt(n)
		if at != want[0] || ok != (want[1] == 1) {
			t.Errorf("ReviewAt(%d) = %d %v", n, at, ok)
		}
	}
	if got := tk.CoveredBy(3); !reflect.DeepEqual(got, []int{1, 2}) {
		t.Error(got)
	}
	tk.Workflow = &Workflow{Order: WorkflowDAG, ReviewAt: map[int]int{2: 3, 1: 3, 4: 5}}
	if got := tk.CoveredBy(3); !reflect.DeepEqual(got, []int{1, 2}) {
		t.Error(got)
	}
	if at, ok := tk.ReviewAt(4); !ok || at != 5 {
		t.Error(at, ok)
	}
	if !tk.InPlan(3) || tk.InPlan(4) || tk.ItemAt(4).Type != "d" {
		t.Error("番号")
	}
}

func TestCombine(t *testing.T) {
	cases := [][3]rules.Decision{
		{rules.Allow, rules.Deny, rules.Deny}, {rules.Ask, rules.Allow, rules.Ask},
		{Outside, rules.Deny, Outside}, {rules.Allow, Outside, Outside},
	}
	for _, c := range cases {
		if got := Combine(c[0], c[1]); got != c[2] {
			t.Errorf("Combine(%q, %q) = %q", c[0], c[1], got)
		}
	}
}

func TestPlanItemRaw(t *testing.T) {
	if (&PlanItem{Type: "a"}).Raw() != "a" {
		t.Error("名前だけ")
	}
	a, b := PlanItem{Type: "a", After: []int{1}}, PlanItem{Type: "a", After: []int{2}}
	if !a.Same(&b) || a.Same(&PlanItem{Type: "a", Review: Needed}) {
		t.Error("Same は after を見ない")
	}
	if ChosenReview(Unsaid, Unneeded) != Unneeded || ChosenReview(Needed, Unneeded) != Needed {
		t.Error("ChosenReview")
	}
}

// 範囲の 1 行は allow・ask の順に並べ、項が無ければ「(空)」と書く（判定・実行後チェック・サブエージェントの終わりで同じ表記）。
func TestScopeLine(t *testing.T) {
	tk := &Ticket{Entries: []Entry{{Decision: rules.Ask, Glob: "b/*"}, {Decision: rules.Allow, Regex: "^a"}, {Decision: rules.Deny, Glob: "c"}}}
	if got := tk.ScopeLine(); got != "^a, b/*" {
		t.Errorf("ScopeLine = %q", got)
	}
	if got := (&Ticket{}).ScopeLine(); got != "(空)" {
		t.Errorf("空の範囲 = %q", got)
	}
}
