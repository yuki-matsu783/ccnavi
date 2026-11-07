package propose

import (
	"encoding/json"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"ccnavi/internal/infra/golden"
	"ccnavi/internal/tickets/testkit"
)

// Python の tickets/proposal/propose と同じワークスペースを 2 つ作って approval-request を打たせ、出力と終了コードが
// 1 字も違わないことを見る。

var proposeCases = [][3]any{
	{"i0001", false, nil}, {"i0001-01-01", true, 0}, {"i0001", false, 1}, {"i0002", false, nil},
	{"i0003", false, nil}, {"i0004", false, nil}, {"nope", false, nil},
}

// proposeWorkspace: i0001 は送れる親子（子のフローの下書きと、ほかの親の提案も置く）。i0002 は after の誤り、
// i0003 は送る提案が無い。i0004 は origin の無いプロジェクトの親（送る先が無い）。
func proposeWorkspace(t *testing.T) string {
	root := testkit.ProposalWorkspace(t, "i0001", "i0002", "i0003")
	remote := filepath.Join(root, "remote.git")
	testkit.Git(t, root, "init", "-q", "--bare", remote)
	testkit.Git(t, root, "remote", "add", "origin", remote)
	todo := func(parent, id, text string) {
		testkit.Put(t, filepath.Join(testkit.TodoDir(root, parent), id+".md"), text)
	}
	todo("i0001", "i0001", testkit.ProposalParent("i0001", []string{"  - research", "  - {type: design, after: [1]}"}))
	todo("i0001", "I0001-02-01", testkit.ProposalChild("i0001-02-01", "i0001", "2", []string{"wip/design/*"}))
	todo("i0001", "i0009", testkit.ProposalParent("i0009", nil))
	testkit.Put(t, filepath.Join(testkit.Worktree(root, "i0001"), "wip", "proposals", "flows", "i0001-02-01.yml"), "flow: x\n")
	testkit.Put(t, filepath.Join(testkit.Worktree(root, "i0001"), "wip", "proposals", "flows", "i0001-02-01.txt"), "x\n")
	todo("i0002", "i0002", testkit.ProposalParent("i0002", []string{"  - research", "  - {type: design, after: [5]}"}))
	return root
}

func TestMatchesPython(t *testing.T) {
	goRoot := proposeWorkspace(t)
	want := golden.Load[[]any](t, "propose")
	conf := testkit.Settings(t, goRoot)
	got := []any{}
	for _, c := range proposeCases {
		var out, errs strings.Builder
		var gate func([]string) int
		if code, ok := c[2].(int); ok {
			gate = func(sending []string) int {
				errs.WriteString(strings.Join(sending, ",") + "\n")
				return code
			}
		}
		code := Run(&out, &errs, conf, goRoot, c[0].(string), c[1].(bool), gate)
		got = append(got, []any{code, strings.ReplaceAll(out.String(), goRoot, "<root>"), strings.ReplaceAll(errs.String(), goRoot, "<root>")})
	}
	data, _ := json.Marshal(got)
	var g []any
	_ = json.Unmarshal(data, &g)
	if !reflect.DeepEqual(g, want) {
		a, _ := json.MarshalIndent(g, "", " ")
		b, _ := json.MarshalIndent(want, "", " ")
		t.Errorf("Python と違う\nGo:\n%s\nPython:\n%s", a, b)
	}
}
