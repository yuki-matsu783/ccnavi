package plan

import (
	"bytes"
	"encoding/json"
	"path/filepath"
	"reflect"
	"testing"

	"ccnavi/internal/infra/golden"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/testkit"
)

// ワークスペースを読ませ、計画の無い親を止める文と、子の無いフェーズの案内が、記録した答え（testdata/plan.json）と
// 1 字も違わないことを見る。
//
//	i0001  計画が 3 つ。1 は承認済みの子、2 は todo/ の提案だけ、3 は子が無い
//	i0002  計画が無い
//	i0003  計画が 1 つで、子がある
//	i0004  プロジェクト p の計画の無い親（置き場はプロジェクト）
func fixture(t *testing.T) string {
	root := testkit.ShapedWorkspace(t)
	for _, id := range []string{"i0001", "i0002", "i0003"} {
		testkit.AddWorktree(t, root, id, "main")
	}
	testkit.Put(t, testkit.Approved(root, "i0001", "doing", "i0001"), testkit.ParentText("i0001", []string{"  - work", "  - work", "  - work"}))
	testkit.Put(t, testkit.Approved(root, "i0001", "doing", "i0001-01-01"), testkit.ChildText("i0001-01-01", "i0001", "1"))
	testkit.Put(t, filepath.Join(testkit.Worktree(root, "i0001"), "wip", "proposals", "todo", "i0001-02-01.md"),
		testkit.ChildText("i0001-02-01", "i0001", "2"))
	testkit.Put(t, testkit.Approved(root, "i0002", "doing", "i0002"), testkit.ParentText("i0002", nil))
	testkit.Put(t, testkit.Approved(root, "i0003", "doing", "i0003"), testkit.ParentText("i0003", []string{"  - work"}))
	testkit.Put(t, testkit.Approved(root, "i0003", "doing", "i0003-01-01"), testkit.ChildText("i0003-01-01", "i0003", "1"))
	return root
}

func TestMatchesGolden(t *testing.T) {
	root := fixture(t)
	project := testkit.ParentText("i0004", nil, "project: p")
	want := golden.Load[[]any](t, "plan")
	conf := testkit.Settings(t, root)
	opened, _ := approval.Scan(conf, root, false, nil)
	found := approval.ByID(opened)
	found["i0004"], _ = ticket.Parse(project)
	got := []any{}
	for _, id := range []string{"i0001", "i0001-01-01", "i0002", "i0003", "i0004"} {
		var e bytes.Buffer
		missing := MissingPlan(&e, found[id])
		notice := []string{}
		for _, line := range ChildlessNotice(conf, root, found[id]) {
			notice = append(notice, testkit.Rel(root, line))
		}
		got = append(got, []any{missing, e.String(), notice})
	}
	data, _ := json.Marshal(got)
	var norm []any
	if err := json.Unmarshal(data, &norm); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(norm, want) {
		t.Errorf("golden と違う\nGo:     %v\ngolden: %v", norm, want)
	}
}
