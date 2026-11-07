package phases

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/testkit"
)

func parsed(t *testing.T, lines ...string) *ticket.Ticket {
	t.Helper()
	text := "---\nversion: 1\nticket: i0001\ntitle: 親\nallow:\n  - match: Write\n    glob: \"src/*\"\n" +
		strings.Join(lines, "\n") + "\n---\n"
	p, problems := ticket.Parse(text)
	if p == nil {
		t.Fatal(problems)
	}
	return p
}

// 計画と先行関係の見本の表（tests/fixtures/plan-waits.json）。決まりを直すときは表も一緒に直す。
func TestPlanWaitsFixture(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join(testkit.RepoRoot(t), "tests", "fixtures", "plan-waits.json"))
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Cases []struct {
			Name     string           `json:"name"`
			Plan     json.RawMessage  `json:"plan"`
			Feedback json.RawMessage  `json:"feedback"`
			Waits    map[string][]int `json:"waits"`
			ReviewAt map[string]int   `json:"review_at"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	if len(fixture.Cases) < 5 {
		t.Fatalf("見本が %d 件しかない", len(fixture.Cases))
	}
	for _, c := range fixture.Cases {
		lines := []string{"plan: " + string(c.Plan)}
		if c.Feedback != nil {
			lines = append(lines, "feedback: "+string(c.Feedback))
		}
		wf := Compute(parsed(t, lines...))
		waits, reviewAt := map[string][]int{}, map[string]int{}
		for n, w := range wf.Waits {
			waits[strconv.Itoa(n)] = ints(w)
		}
		for n, at := range wf.ReviewAt {
			reviewAt[strconv.Itoa(n)] = at
		}
		if !reflect.DeepEqual(waits, c.Waits) || !reflect.DeepEqual(reviewAt, c.ReviewAt) {
			t.Errorf("%s: waits %v review_at %v", c.Name, waits, reviewAt)
		}
	}
}

func TestWorkflowIsComputedOnlyWhenMissing(t *testing.T) {
	p := parsed(t, "plan: [a, b]")
	if got := WaitsOf(p, 2); len(got) != 0 {
		t.Errorf("after の無いステップに先行 %v", got)
	}
	// 計画に無い番号は前の番号を全部先行とする（止まる側）。
	if got := WaitsOf(p, 4); !reflect.DeepEqual(got, []int{1, 2, 3}) {
		t.Errorf("計画に無い番号 %v", got)
	}
	p.Workflow = &ticket.Workflow{Waits: map[int][]int{2: {1}}}
	if got := WaitsOf(p, 2); !reflect.DeepEqual(got, []int{1}) {
		t.Errorf("入れた先行関係を読まない %v", got)
	}
}

func idsOf(types *Types) []string {
	var out []string
	for p := range types.All() {
		out = append(out, p.ID+"@"+p.Source)
	}
	return out
}

func TestLayerTypes(t *testing.T) {
	root := t.TempDir()
	conf := &config.Settings{Phases: filepath.Join(root, ".ccnavi", "config", "common", "phases.yml")}
	layer := filepath.Join(root, ".ccnavi", "config", "project", "phases.yml")
	// どちらも無ければ定義が無い（番号だけの挙動）。
	if types, notes := LayerTypes(conf, root, ""); types != nil || notes != nil {
		t.Errorf("無いのに %v %v", idsOf(types), notes)
	}
	testkit.Put(t, conf.Phases, defs("  a: {title: A}", "  b: {title: B}"))
	if got := idsOf(LoadTypes(conf, root, "")); !reflect.DeepEqual(got, []string{"a@common", "b@common"}) {
		t.Errorf("共通だけ %v", got)
	}
	testkit.Put(t, layer, defs("  a: {title: A}", "  c: {title: B}", "  d: {title: D}"))
	types, notes := LayerTypes(conf, root, "")
	if got := idsOf(types); !reflect.DeepEqual(got, []string{"a@common", "b@common", "d@self"}) {
		t.Errorf("和 %v", got)
	}
	if len(notes) != 2 || notes[0].Severity != rules.Info || notes[1].Severity != rules.Error {
		t.Errorf("苦情 %v", notes)
	}
	// 予約名のプロジェクトはレイヤーとして数えない。
	if TypesPath(conf, root, config.LayerSelf) != "" || TypesPath(conf, root, config.LayerCommon) != "" {
		t.Error("予約名にパスがある")
	}
	// 壊れたレイヤーは空として扱い、そのレイヤーの苦情を返す。
	testkit.Put(t, layer, "version: 1\nphases: [\n")
	types, notes = LayerTypes(conf, root, "")
	if got := idsOf(types); !reflect.DeepEqual(got, []string{"a@common", "b@common"}) || len(notes) != 1 {
		t.Errorf("壊れたレイヤー %v %v", got, notes)
	}
	// 共通レイヤーが壊れていれば、レイヤーの定義だけを使う（共通レイヤーの苦情は返さない）。
	testkit.Put(t, conf.Phases, "\xff")
	testkit.Put(t, layer, defs("  d: {}"))
	types, notes = LayerTypes(conf, root, "")
	if got := idsOf(types); !reflect.DeepEqual(got, []string{"d@self"}) || len(notes) != 0 {
		t.Errorf("壊れた共通 %v %v", got, notes)
	}
	if _, notes := CommonTypes(conf); len(notes) != 1 || !strings.Contains(notes[0].Detail, "を読めない ('utf-8' codec can't decode byte 0xff") {
		t.Errorf("読めない共通 %v", notes)
	}
}

func TestTypesOfMarksTheParentLayer(t *testing.T) {
	p := parsed(t, "phases:", "  a: {title: A}", "  b: {kind: x}", "plan: [a]")
	types := TypesOf(p)
	if got := idsOf(types); !reflect.DeepEqual(got, []string{"a@self"}) {
		t.Errorf("%v", got)
	}
	p.Project = "web"
	if got := idsOf(TypesOf(p)); !reflect.DeepEqual(got, []string{"a@web"}) || TypesOf(p) != types {
		t.Errorf("引き直し %v", got)
	}
	common, _ := Parse(defs("  a: {title: A}"), Where)
	if Origin(types.Get("a"), common) != config.LayerCommon || Origin(types.Get("a"), nil) != "web" {
		t.Error("Origin")
	}
	if TypesOf(parsed(t)).Len() != 0 || HasCopy(parsed(t)) {
		t.Error("コピーが無いのに定義がある")
	}
}

func TestConsolidateType(t *testing.T) {
	p := ConsolidateType()
	if p.ID != ticket.MaintenanceConsolidate || p.Kind != Feedback || p.Review || p.Learning != LearningOff || p.Inherit {
		t.Errorf("%#v", p)
	}
	if p.Decide("docs/wiki/inbox/a.md") != rules.Allow || p.Decide("src/a.go") != ticket.Outside {
		t.Error("範囲")
	}
}
