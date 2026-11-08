package candidates

import (
	"encoding/json"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"ccnavi/internal/infra/golden"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/testkit"
)

// 承認の候補の検査を見本（チケットの文面）とワークスペースに当て、苦情が記録した答え（testdata/candidates.json）と
// 1 字も違わないことを見る。承認の全体の流れ（Candidates）は agree の突き合わせが見る。

var plan3 = []string{"  - research", "  - {type: design, after: [1]}", "  - {type: implement, after: [2]}"}

func sampleTickets() map[string]string {
	plan4 := []string{"  - design", "  - {type: acceptance, after: [1]}", "  - {type: implement, after: [1]}",
		"  - {type: docs, after: [2, 3]}"}
	bare := func(id string, plan []string, extra ...string) string {
		text := "---\nversion: 1\nticket: " + id + "\nplan:\n" + strings.Join(plan, "\n") + "\n"
		for _, e := range extra {
			text += e + "\n"
		}
		return text + "title: 親\nallow:\n  - match: Write|Edit\n    glob: \"src/*\"\n---\n本文\n"
	}
	return map[string]string{
		"parent":       testkit.ProposalParent("i0001", plan3),
		"deferBad":     testkit.ProposalParent("i0001", []string{"  - {type: research, deferred: true}", "  - {type: design, after: [1]}"}),
		"copyDiff":     strings.Replace(testkit.ProposalParent("i0001", plan3), "title: 設計\n", "title: 設計 別\n", 1),
		"noPlan":       testkit.ProposalParent("i0001", nil),
		"noCopy":       bare("i0001", plan3),
		"maint":        testkit.ProposalParent("i0001", plan3, "maintenance: learning-consolidate"),
		"child1":       testkit.ProposalChild("i0001-01-01", "i0001", "1", []string{"wip/research/*"}),
		"childOver":    testkit.ProposalChild("i0001-01-02", "i0001", "1", []string{"docs/*", "wip/research/a/*"}),
		"childRegex":   testkit.ProposalChild("i0001-01-03", "i0001", "1", nil, "  - match: Write|Edit\n    regex: \"wip/.*\""),
		"childBad":     testkit.ProposalChild("i0001-05-01", "i0001", "5", []string{"src/*"}),
		"childMaint":   testkit.ProposalChild("i0001-00-01", "i0001", "0", []string{"docs/wiki/inbox/*", "src/*"}, "maintenance: learning-consolidate"),
		"childNoCopy":  testkit.ProposalChild("i0001-02-01", "i0001", "2", []string{"src/*"}),
		"fields":       testkit.ProposalParent("i0001", plan3, "workflow: dag", "started_at: false", "base_sha: \" \"", "followup_of: x", "human_review: false"),
		"hashIssue":    strings.Replace(testkit.ProposalParent("i0001", plan3), "issue: 1\n", "issue: \" #12 \"\n", 1),
		"lockCurrent":  bare("i0009", plan4),
		"lockType":     bare("i0009", []string{"  - design", "  - {type: docs, after: [1]}", "  - {type: implement, after: [1]}", "  - {type: docs, after: [2, 3]}"}),
		"lockAfter":    bare("i0009", []string{"  - design", "  - {type: acceptance, after: [1]}", "  - {type: implement}", "  - {type: docs, after: [2, 3]}"}),
		"lockSwap":     bare("i0009", []string{"  - implement", "  - {type: acceptance, after: [1]}", "  - {type: implement, after: [1]}", "  - {type: docs, after: [2, 3]}"}),
		"lockDeferred": bare("i0009", []string{"  - design", "  - {type: acceptance, after: [1], deferred: true}", "  - {type: implement, after: [1]}", "  - {type: docs, after: [2, 3]}"}),
		"revScope":     strings.Replace(testkit.ProposalParent("i0002", plan3), "    glob: \"wip/*\"\n", "    glob: \"wip/*\"\n  - match: Write\n    glob: \"/abs\"\n", 1),
		"revTitle":     strings.Replace(strings.Replace(testkit.ProposalParent("i0002", plan3[:2]), "title: 親 i0002", "title: 別", 1), "mr: 1\n", "mr: 2\n", 1),
		"revFeedback":  testkit.ProposalParent("i0002", plan3[:2], "feedback: [implement]"),
		"revBusy":      strings.Replace(testkit.ProposalParent("i0002", plan3[:2]), "allow:\n", "allow:\n  - match: Write|Edit\n    glob: \"tests/*\"\n", 1),
		"revLocked":    testkit.ProposalParent("i0002", []string{"  - design", "  - {type: research, after: [1]}"}),
	}
}

var validateCases = [][5]any{
	{"parent", []string{}, true, false, ""}, {"parent", []string{}, false, true, ""}, {"maint", []string{}, true, false, ""},
	{"child1", []string{"parent"}, true, false, ""}, {"childOver", []string{"parent"}, true, false, ""},
	{"childRegex", []string{"parent"}, true, false, ""}, {"childBad", []string{"parent"}, true, false, ""},
	{"childMaint", []string{"parent"}, true, true, ""}, {"childMaint", []string{"parent"}, true, false, ""},
	{"child1", []string{}, true, false, ""}, {"child1", []string{}, true, false, "親はよそに在る"},
	{"childNoCopy", []string{"noCopy"}, true, false, ""},
}

var planCases = [][3]any{
	{"parent", nil, false}, {"deferBad", nil, false}, {"copyDiff", nil, false}, {"copyDiff", nil, true},
	{"copyDiff", []string{"parent", "design"}, false}, {"noPlan", nil, false}, {"noPlan", []string{"parent"}, false},
	{"noCopy", nil, false}, {"childNoCopy", nil, false},
}

var lockCases = [][4]any{
	{"lockCurrent", "lockCurrent", []int{1, 3}, nil}, {"lockType", "lockCurrent", []int{1, 3}, nil},
	{"lockAfter", "lockCurrent", []int{1, 3}, nil}, {"lockSwap", "lockCurrent", []int{3}, nil},
	{"lockDeferred", "lockCurrent", []int{4}, nil}, {"lockDeferred", "lockCurrent", []int{0, 2}, map[string]int{"1": 1}},
}

var revisionCases = []string{"revScope", "revTitle", "revFeedback", "revLocked", "revBusy"}

// candidatesWorkspace: i0002 は承認済みの親で、2 番の子が作業中（範囲の改版を止める）。i0003 の子の提案は、親
// i0003 の提案がワークスペースルートの todo/ にある（親のワークツリーの外）。i0010 はワークスペースに同じ名前の
// ブランチがある新規の親、i0001 は自分のワークツリーの中の新規の親（自分のブランチなので言わない）。
func candidatesWorkspace(t *testing.T) string {
	root := testkit.ProposalWorkspace(t, "i0001", "i0002", "i0003")
	testkit.Git(t, root, "branch", "I0010")
	testkit.Git(t, root, "branch", "feature/x")
	testkit.Put(t, filepath.Join(testkit.ApprovedDir(root, "i0002"), "doing", "i0002.md"), testkit.ProposalParent("i0002", plan3[:2]))
	testkit.Put(t, filepath.Join(testkit.ApprovedDir(root, "i0002"), "doing", "i0002-02-01.md"),
		testkit.ProposalChild("i0002-02-01", "i0002", "2", []string{"wip/design/*"}, `started_at: "2026-01-01T00:00:00+0900"`))
	testkit.Put(t, filepath.Join(testkit.ApprovedDir(root, "i0002"), "done", "i0002-01-01.md"),
		testkit.ProposalChild("i0002-01-01", "i0002", "1", []string{"wip/research/*"}, `completed_at: "2026-01-01T00:00:00+0900"`))
	testkit.Put(t, filepath.Join(testkit.TodoDir(root, "i0001"), "i0001.md"), testkit.ProposalParent("i0001", plan3))
	testkit.Put(t, filepath.Join(testkit.TodoDir(root, "i0003"), "i0003-01-01.md"), testkit.ProposalChild("i0003-01-01", "i0003", "1", []string{"src/*"}))
	testkit.Put(t, filepath.Join(root, "wip", "proposals", "todo", "i0003.md"), testkit.ProposalParent("i0003", plan3))
	testkit.Put(t, filepath.Join(root, "wip", "proposals", "todo", "i0010.md"), testkit.ProposalParent("i0010", plan3))
	testkit.Put(t, filepath.Join(root, "wip", "proposals", "todo", "feature-x.md"), testkit.ProposalParent("feature-x", plan3))
	return root
}

func TestMatchesGolden(t *testing.T) {
	goRoot := candidatesWorkspace(t)
	samples := sampleTickets()
	want := golden.Load[map[string]any](t, "candidates")
	got := goAnswers(t, goRoot, samples)
	for key, w := range want {
		if g := normalize(t, got[key]); !reflect.DeepEqual(g, w) {
			a, _ := json.MarshalIndent(g, "", " ")
			b, _ := json.MarshalIndent(w, "", " ")
			t.Errorf("%s が golden と違う\nGo:\n%s\ngolden:\n%s", key, a, b)
		}
	}
}

func normalize(t *testing.T, v any) any {
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

func goAnswers(t *testing.T, root string, samples map[string]string) map[string]any {
	rel := func(s string) string { return strings.ReplaceAll(s, root, "<root>") }
	ps := func(problems []rules.Problem) [][]string {
		out := [][]string{}
		for _, p := range problems {
			out = append(out, []string{string(p.Severity), p.Rule, rel(p.Detail), p.Kind})
		}
		return out
	}
	parse := func(name string) *ticket.Ticket {
		found, problems := ticket.Parse(samples[name])
		if found == nil {
			t.Fatalf("%s が読めない: %v", name, problems)
		}
		return found
	}
	conf := testkit.Settings(t, root)
	cfg := phases.LoadTypes(conf, root, "")
	fields := map[string]any{}
	for name := range samples {
		fields[name] = ps(fieldProblems(parse(name)))
	}
	var validated []any
	for _, c := range validateCases {
		pool := map[string]*ticket.Ticket{}
		for _, n := range c[1].([]string) {
			p := parse(n)
			pool[p.ID] = p
		}
		var use *phases.Types
		if c[2].(bool) {
			use = cfg
		}
		a, b := Validate(parse(c[0].(string)), pool, use, c[3].(bool), c[4].(string))
		validated = append(validated, []any{ps(a), ps(b)})
	}
	var planned []any
	for _, c := range planCases {
		var held map[string]*phases.PhaseType
		if names, ok := c[1].([]string); ok {
			held = map[string]*phases.PhaseType{}
			types := phases.TypesOf(parse(names[0]))
			for _, n := range names[1:] {
				held[n] = types.Get(n)
			}
		}
		planned = append(planned, ps(PlanProblems(parse(c[0].(string)), cfg, held, c[2].(bool))))
	}
	var locked []any
	for _, c := range lockCases {
		fixed := map[int]bool{}
		for _, n := range c[2].([]int) {
			fixed[n] = true
		}
		var moved map[int]int
		if m, ok := c[3].(map[string]int); ok {
			moved = map[int]int{}
			for k, v := range m {
				moved[int(k[0]-'0')] = v
			}
		}
		locked = append(locked, ps(LockProblems(parse(c[0].(string)), parse(c[1].(string)), fixed, moved)))
	}
	copies, _ := approval.Scan(conf, root, false, nil)
	byID := approval.ByID(copies)
	var revised []any
	for _, name := range revisionCases {
		r := parse(name)
		revised = append(revised, ps(revisionProblems(conf, root, r, byID[r.ID], cfg)))
	}
	proposals := approval.ReadProposals(conf, root, nil).Kept
	sorted := append([]*ticket.Ticket(nil), proposals...)
	sortTickets(sorted)
	var elsewhere []any
	for _, p := range sorted {
		elsewhere = append(elsewhere, []string{p.ID, rel(ParentElsewhereText(conf, root, p, nil))})
	}
	branches := []string{}
	for _, b := range ExistingBranchWarnings(conf, root, sorted, copies) {
		branches = append(branches, rel(b))
	}
	return map[string]any{"fields": fields, "validate": validated, "plan": planned, "lock": locked,
		"revision": revised, "elsewhere": elsewhere, "branches": branches}
}

func sortTickets(ts []*ticket.Ticket) {
	for i := 1; i < len(ts); i++ {
		for j := i; j > 0 && (ts[j].ID < ts[j-1].ID || (ts[j].ID == ts[j-1].ID && ts[j].Tree < ts[j-1].Tree)); j-- {
			ts[j], ts[j-1] = ts[j-1], ts[j]
		}
	}
}
