package planedit

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"ccnavi/internal/infra/golden"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/testkit"
)

// Python の plan_order と plan_phases と同じワークスペースを 2 つ作って読ませ、--plan-order の JSON・保存の結果と
// 書いたバイト列・--order の読みの苦情・--fill-phases の結果と書いたバイト列が 1 字も違わないことを見る。

// planWorkspace は Python と Go に読ませるワークスペース。
//
//	i0001 新規の親（4 ステップ、2 と 3 が 1 の後、4 が 2 と 3 の後）。2 番と 3 番に子の提案、フローの下書き、
//	      ほかの親の子（i0005-01-01）が 3 番の子を predecessors で指す。値の外にコメントを置く
//	i0002 承認済みの親の改版の提案（3 ステップ）。2 番に子が承認されている（固定した番号）
//	i0003 phases: のコピーが計画と合わない新規の親（fill で差し替える）。i0004 は計画の無い親、i0006 は phases.yml に
//	      無い定義を使う親
func planWorkspace(t *testing.T) string {
	root := testkit.ProposalWorkspace(t, "i0001", "i0002", "i0003", "i0004", "i0005", "i0006")
	todo := func(parent, id, text string) {
		testkit.Put(t, filepath.Join(testkit.TodoDir(root, parent), id+".md"), text)
	}
	plan4 := []string{"  - research", "  - {type: design, after: [1]}", "  # 値の中のコメント", "  - {type: implement, after: [1]}",
		"  - {type: implement, after: [2, 3]}"}
	todo("i0001", "i0001", strings.Replace(testkit.ProposalParent("i0001", plan4), "human_review:", "# 値の外のコメント\nhuman_review:", 1))
	todo("i0001", "i0001-02-01", testkit.ProposalChild("i0001-02-01", "i0001", "2", []string{"wip/design/*"}))
	todo("i0001", "i0001-03-01", testkit.ProposalChild("i0001-03-01", "i0001", "3", []string{"src/*"}, "predecessors: [i0001-02-01]"))
	testkit.Put(t, filepath.Join(testkit.Worktree(root, "i0001"), "wip", "proposals", "flows", "i0001-03-01.yml"), "flow: x\n")
	todo("i0005", "i0005-01-01", testkit.ProposalChild("i0005-01-01", "i0005", "1", []string{"src/*"}, "predecessors: [i0001-03-01]"))

	plan3 := []string{"  - research", "  - {type: design, after: [1]}", "  - {type: implement, after: [1]}"}
	approvedCopy := strings.Replace(testkit.ProposalParent("i0002", plan3), "title: 設計\n", "title: 設計 承認時\n", 1)
	testkit.Put(t, filepath.Join(testkit.ApprovedDir(root, "i0002"), "doing", "i0002.md"), approvedCopy)
	testkit.Put(t, filepath.Join(testkit.ApprovedDir(root, "i0002"), "done", "i0002-02-01.md"),
		testkit.ProposalChild("i0002-02-01", "i0002", "2", []string{"wip/design/*"}, `completed_at: "2026-01-01T00:00:00+0900"`))
	todo("i0002", "i0002", testkit.ProposalParent("i0002", plan3))

	third := strings.Replace(testkit.ProposalParent("i0003", []string{"  - research", "  - design"}), "  implement:\n", "  # 定義の間のコメント\n  implement:\n", 1)
	todo("i0003", "i0003", strings.Replace(third, "title: 調査\n", "title: 調べる\n", 1))
	todo("i0004", "i0004", testkit.ProposalParent("i0004", nil))
	todo("i0006", "i0006", testkit.ProposalParent("i0006", []string{"  - research", "  - {type: deploy, after: [1]}"}))
	return root
}

var orderCases = [][2]any{
	{"i0001", nil},
	{"i0001", `{"plan": {"2": [1], "3": [1], "4": [2, 3]}}`},
	{"i0001", `{"plan": {"2": [3], "3": [1], "4": [2]}}`},
	{"i0001", `{"plan": {"2": [3], "3": [2], "4": [2]}}`},
	{"i0001", `{"plan": {"4": [1]}}`},
	{"i0002", `{"plan": {"2": [1, 3], "3": [1]}}`},
	{"i0002", `{"plan": {"1": [3], "2": [1]}}`},
	{"i0002", `{"plan": {"2": [1], "3": [2]}}`},
	{"i0001", `[1]`},
	{"i0001", `{"plan": {`},
	{"i0001", `{"feedback": {}}`},
	{"i0001", `{"other": {}}`},
	{"i0001", `{"plan": []}`},
	{"i0001", `{"plan": {"x": []}}`},
	{"i0001", `{"plan": {"9": []}}`},
	{"i0001", `{"plan": {"2": 1}}`},
	{"i0001", `{"plan": {"2": [true]}}`},
	{"i0001", `{"plan": {"2": [1.5]}}`},
	{"i0001", `{"plan": {"2": ["1"]}}`},
	{"i0001", `{"plan": {"2": [2]}}`},
	{"i0001", `{"plan": {"2": [7]}}`},
	{"i0001", `{"plan": {"2": [99999999999999999999999]}}`},
}

var saveCases = [][3]any{
	{"i0001", `{"plan": {"2": [3], "3": [1], "4": [2]}}`, false},
	{"i0002", `{"plan": {"2": [1, 3], "3": [1]}}`, true},
	{"i0001", `{"plan": {"2": [1], "3": [1], "4": [2, 3]}}`, true},
	{"i0001", `{"plan": {"2": [3], "3": [1], "4": [2]}}`, true},
}

var fillCases = []string{"i0003", "i0002", "i0004", "i0006", "i0003"}

func TestMatchesPython(t *testing.T) {
	goRoot := planWorkspace(t)
	want := golden.Load[map[string]any](t, "plan")
	got := goAnswers(t, goRoot)
	for key, w := range want {
		if g := normalize(t, got[key]); !reflect.DeepEqual(g, w) {
			a, _ := json.MarshalIndent(g, "", " ")
			b, _ := json.MarshalIndent(w, "", " ")
			t.Errorf("%s が Python と違う\nGo:\n%s\nPython:\n%s", key, a, b)
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

func list[T any](xs []T) []T {
	if xs == nil {
		return []T{}
	}
	return xs
}

func goAnswers(t *testing.T, root string) map[string]any {
	rel := func(s string) string { return strings.ReplaceAll(s, root, "<root>") }
	rels := func(ss []string) []string {
		out := []string{}
		for _, s := range ss {
			out = append(out, rel(s))
		}
		return out
	}
	conf := testkit.Settings(t, root)
	source := func(name string) *Source {
		proposals := approval.ReadProposals(conf, root, nil).Kept
		target, _ := Pick(proposals, name, root)
		s, why := Load(conf, root, target, proposals, CurrentParent(conf, root, name))
		if s == nil {
			t.Fatal(why)
		}
		return s
	}
	bodies, parses := []string{}, []string{}
	for _, c := range orderCases {
		s := source(c[0].(string))
		var order Order
		if text, ok := c[1].(string); ok {
			var why string
			if order, why = ParseOrder(text, s.Parent); order == nil {
				parses = append(parses, why)
				continue
			}
		}
		body, err := pyjson.Dumps(Reorder(s, order).Body(), pyjson.Options{RawUnicode: true})
		if err != nil {
			t.Fatal(err)
		}
		bodies = append(bodies, rel(body))
	}
	saved := []any{}
	for _, c := range saveCases {
		name := c[0].(string)
		s := source(name)
		order, _ := ParseOrder(c[1].(string), s.Parent)
		expect := "x"
		if c[2].(bool) {
			expect = s.SHA()
		}
		r := Reorder(s, order).Save(expect)
		saved = append(saved, []any{r.OK, r.Written, rels(r.Messages), rels(r.Details), RereadSHA(conf, root, name, s.Current, root) != ""})
	}
	filled := []any{}
	for _, name := range fillCases {
		target, _ := Pick(approval.ReadProposals(conf, root, nil).Kept, name, root)
		done := Fill(conf, root, target, CurrentParent(conf, root, name))
		filled = append(filled, []any{rel(done.Why), done.Changed, list(done.Copied), list(done.Dropped), list(done.Held)})
	}
	files := map[string]string{}
	err := filepath.Walk(filepath.Join(root, ".claude"), func(path string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() {
			return err
		}
		if (strings.HasSuffix(path, ".md") || strings.HasSuffix(path, ".yml")) &&
			strings.Contains(filepath.Dir(path)+string(filepath.Separator), string(filepath.Separator)+"proposals"+string(filepath.Separator)) {
			data, err := os.ReadFile(path)
			if err != nil {
				return err
			}
			files[rel(path)] = string(data)
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return map[string]any{"bodies": bodies, "parse": parses, "saved": saved, "filled": filled, "files": files}
}
