package planedit

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/model/pyoracle"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/proposal/candidates"
)

// 振り直しの決まりは拡張の計画編集タブと共有する見本の表（tests/fixtures/plan-reorder.json）で固定する。

// parsedPlan は計画のステップ（見本の表の形。JSON は YAML の flow として読める）を持つ親。
func parsedPlan(t *testing.T, plan, feedback any) *ticket.Ticket {
	t.Helper()
	text := "---\nversion: 1\nticket: i0001\nplan: " + jsonText(t, plan) + "\n"
	if feedback != nil {
		text += "feedback: " + jsonText(t, feedback) + "\n"
	}
	text += "title: 親\nallow:\n  - match: Write|Edit\n    glob: \"src/*\"\n---\n本文\n"
	found, problems := ticket.Parse(text)
	if found == nil {
		t.Fatalf("読めない: %v", problems)
	}
	return found
}

func jsonText(t *testing.T, v any) string {
	t.Helper()
	data, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

// raws は PlanItem.Raw を JSON の値として読み直したもの（キーの順を見ずに比べる）。
func raws(t *testing.T, items []ticket.PlanItem) any {
	t.Helper()
	out := []any{}
	for i := range items {
		text, err := pyjson.Dumps(items[i].Raw(), pyjson.Options{})
		if err != nil {
			t.Fatal(err)
		}
		var v any
		if err := json.Unmarshal([]byte(text), &v); err != nil {
			t.Fatal(err)
		}
		out = append(out, v)
	}
	return out
}

type reorderCase struct {
	Name     string `json:"name"`
	Plan     any    `json:"plan"`
	Feedback any    `json:"feedback"`
	Order    any    `json:"order"`
	Fixed    []int  `json:"fixed"`
	Refused  string `json:"refused"`
	Children []struct {
		Ticket       string   `json:"ticket"`
		Phase        int      `json:"phase"`
		Predecessors []string `json:"predecessors"`
	} `json:"children"`
	Expect struct {
		Plan     any   `json:"plan"`
		Feedback any   `json:"feedback"`
		From     []int `json:"from"`
		Children []any `json:"children"`
	} `json:"expect"`
}

func TestReorderTable(t *testing.T) {
	data, err := os.ReadFile(filepath.Join(pyoracle.RepoRoot(t), "tests", "fixtures", "plan-reorder.json"))
	if err != nil {
		t.Fatal(err)
	}
	var table struct {
		Cases []reorderCase `json:"cases"`
	}
	if err := json.Unmarshal(data, &table); err != nil {
		t.Fatal(err)
	}
	if len(table.Cases) == 0 {
		t.Fatal("見本が無い")
	}
	for _, c := range table.Cases {
		t.Run(c.Name, func(t *testing.T) {
			parent := parsedPlan(t, c.Plan, c.Feedback)
			order, why := ParseOrder(jsonText(t, c.Order), parent)
			if why != "" {
				t.Fatal(why)
			}
			fixed := map[int]bool{}
			for _, n := range c.Fixed {
				fixed[n] = true
			}
			got := Renumber(parent, order, fixed)
			if c.Refused != "" {
				if len(got.Refused) != 1 || string(got.Refused[0].Kind) != c.Refused {
					t.Fatalf("refused = %v, want %s", got.Refused, c.Refused)
				}
				return
			}
			if len(got.Refused) > 0 {
				t.Fatalf("断った: %v", got.Refused)
			}
			if g := raws(t, got.Plan); !reflect.DeepEqual(g, c.Expect.Plan) {
				t.Errorf("plan = %v, want %v", g, c.Expect.Plan)
			}
			if c.Expect.Feedback != nil {
				if g := raws(t, got.Feedback); !reflect.DeepEqual(g, c.Expect.Feedback) {
					t.Errorf("feedback = %v, want %v", g, c.Expect.Feedback)
				}
			}
			back := map[int]int{}
			for old, renumbered := range got.Moved {
				back[renumbered] = old
			}
			var from []int
			for n := 1; n <= len(back); n++ {
				from = append(from, back[n])
			}
			if !slices.Equal(from, c.Expect.From) {
				t.Errorf("from = %v, want %v", from, c.Expect.From)
			}
			if c.Children == nil {
				return
			}
			var rows []row
			for _, ch := range c.Children {
				rows = append(rows, row{ch.Ticket, ch.Phase, ch.Predecessors})
			}
			changed := []any{}
			for _, r := range retarget("i0001", got.Moved, rows) {
				changed = append(changed, map[string]any{"ticket": r.ticket, "new_ticket": r.newTicket,
					"phase": phaseOfRow(rows, r.ticket), "new_phase": r.newPhase,
					"predecessors": predsOfRow(rows, r.ticket), "new_predecessors": r.newPredecessors})
			}
			if g, w := jsonText(t, changed), jsonText(t, c.Expect.Children); g != w {
				t.Errorf("children = %s, want %s", g, w)
			}
		})
	}
}

func phaseOfRow(rows []row, id string) any {
	for _, r := range rows {
		if r.ticket == id {
			return r.phase
		}
	}
	return nil
}

func predsOfRow(rows []row, id string) []string {
	for _, r := range rows {
		if r.ticket == id {
			return r.predecessors
		}
	}
	return nil
}

var plan3 = []any{"research", map[string]any{"type": "design", "after": []int{1}}, map[string]any{"type": "implement", "after": []int{2}}}

func TestWithoutOrderNothingMoves(t *testing.T) {
	parent := parsedPlan(t, plan3, nil)
	got := Renumber(parent, nil, nil)
	if len(got.Refused) > 0 || !reflect.DeepEqual(got.Moved, map[int]int{1: 1, 2: 2, 3: 3}) {
		t.Fatalf("got %+v", got)
	}
	if g, w := jsonText(t, raws(t, got.Plan)), jsonText(t, plan3); g != w {
		t.Errorf("plan = %s, want %s", g, w)
	}
}

// 回帰: 深い入れ子の JSON で落ちずに、読めないと言う。
func TestBadOrdersAreNamed(t *testing.T) {
	parent := parsedPlan(t, plan3, []any{"implement"})
	for _, c := range []struct{ text, word string }{
		{"[", "JSON"}, {"[]", "plan"}, {`{"steps": {}}`, "steps"}, {`{"plan": []}`, "plan"},
		{`{"plan": {"x": []}}`, "x"}, {`{"plan": {"4": []}}`, "4"}, {`{"plan": {"2": [2]}}`, "自分"},
		{`{"plan": {"2": [9]}}`, "9"}, {`{"plan": {"2": [true]}}`, "True"}, {`{"plan": {"2": ["1"]}}`, "'1'"},
		{`{"plan": {"2": 1}}`, "リスト"}, {`{"feedback": {"4": [1]}}`, "1"}, {`{"feedback": {"3": []}}`, "3"},
		{`{"plan": {"²": []}}`, "²"}, {`{"plan": {"０": []}}`, "０"},
		{strings.Repeat("[", 100000) + strings.Repeat("]", 100000), "JSON"},
	} {
		order, why := ParseOrder(c.text, parent)
		if order != nil || !strings.Contains(why, c.word) {
			t.Errorf("%.40s: order=%v why=%q（%q を含まない）", c.text, order, why, c.word)
		}
	}
	if order, why := ParseOrder(`{"feedback": {}}`, parsedPlan(t, plan3, nil)); order != nil || !strings.Contains(why, "feedback") {
		t.Errorf("feedback の無い提案: %v %q", order, why)
	}
	order, why := ParseOrder(`{"plan": {"3": [1, 1, 2]}}`, parent)
	if why != "" || !slices.Equal(order["plan"][3], []int{1, 2}) {
		t.Errorf("重ねた番号: %v %q", order, why)
	}
}

// 延期の引き受け手は、元のステップの同一性（振り直す前の番号）で比べる。
func TestMovedDeferredItemIsComparedWithItsOwnTarget(t *testing.T) {
	current := parsedPlan(t, []any{"research", map[string]any{"type": "design", "deferred": true},
		map[string]any{"type": "implement", "after": []int{1, 2}}}, nil)
	revised := parsedPlan(t, []any{map[string]any{"type": "design", "deferred": true}, "research",
		map[string]any{"type": "implement", "after": []int{1, 2}}}, nil)
	deferred := func(ps []string) bool {
		return slices.ContainsFunc(ps, func(s string) bool { return strings.Contains(s, "延期") })
	}
	details := func(moved map[int]int) []string {
		var out []string
		for _, p := range candidates.LockProblems(revised, current, map[int]bool{3: true}, moved) {
			out = append(out, p.Detail)
		}
		return out
	}
	if got := details(map[int]int{1: 2, 2: 1, 3: 3}); deferred(got) {
		t.Errorf("振り直しを渡しても延期で止まる: %v", got)
	}
	if got := details(nil); !deferred(got) {
		t.Errorf("番号だけで比べると延期で止まるはず: %v", got)
	}
}
