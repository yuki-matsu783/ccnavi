package plan

import (
	"bytes"
	"encoding/json"
	"path/filepath"
	"reflect"
	"testing"

	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/lifecycle/internal/gitfixture"
	"ccnavi/internal/tickets/model/pyoracle"
	"ccnavi/internal/tickets/model/ticket"
)

// Python の plan と同じワークスペースを読ませ、計画の無い親を止める文と、子の無いフェーズの案内が 1 字も違わない
// ことを見る。
//
//	i0001  計画が 3 つ。1 は承認済みの子、2 は todo/ の提案だけ、3 は子が無い
//	i0002  計画が無い
//	i0003  計画が 1 つで、子がある
//	i0004  プロジェクト p の計画の無い親（置き場はプロジェクト）
func fixture(t *testing.T) string {
	root := gitfixture.Workspace(t)
	for _, id := range []string{"i0001", "i0002", "i0003"} {
		gitfixture.AddWorktree(t, root, id, "main")
	}
	gitfixture.Put(t, gitfixture.Approved(root, "i0001", "doing", "i0001"), gitfixture.ParentText("i0001", []string{"  - work", "  - work", "  - work"}))
	gitfixture.Put(t, gitfixture.Approved(root, "i0001", "doing", "i0001-01-01"), gitfixture.ChildText("i0001-01-01", "i0001", "1"))
	gitfixture.Put(t, filepath.Join(gitfixture.Worktree(root, "i0001"), "wip", "proposals", "todo", "i0001-02-01.md"),
		gitfixture.ChildText("i0001-02-01", "i0001", "2"))
	gitfixture.Put(t, gitfixture.Approved(root, "i0002", "doing", "i0002"), gitfixture.ParentText("i0002", nil))
	gitfixture.Put(t, gitfixture.Approved(root, "i0003", "doing", "i0003"), gitfixture.ParentText("i0003", []string{"  - work"}))
	gitfixture.Put(t, gitfixture.Approved(root, "i0003", "doing", "i0003-01-01"), gitfixture.ChildText("i0003-01-01", "i0003", "1"))
	return root
}

const oracle = `
import io, json, sys
from ccnavi.infra import settings
from ccnavi.tickets.approval import store as approval
from ccnavi.tickets.lifecycle import plan
from ccnavi.tickets.model import ticket as tm
c = json.load(sys.stdin)
root = c["root"]
conf, _ = settings.load(root)
found = {t.ticket: t for t in approval.scan(conf, root)[0]}
found["i0004"] = tm.parse(c["project"])[0]
out = []
for ident in ("i0001", "i0001-01-01", "i0002", "i0003", "i0004"):
    e = io.StringIO()
    missing = plan.plan_missing(e, found[ident])
    out.append([missing, e.getvalue(), [x.replace(root, "<root>") for x in plan.childless_notice(root, conf, found[ident])]])
json.dump(out, sys.stdout, ensure_ascii=False)
`

func TestMatchesPython(t *testing.T) {
	root := fixture(t)
	project := gitfixture.ParentText("i0004", nil, "project: p")
	want := pyoracle.Answers[[]any](t, "plan", oracle, map[string]any{"root": root, "project": project})
	conf := gitfixture.Settings(t, root)
	opened, _ := approval.Scan(conf, root, false, nil)
	found := approval.ByID(opened)
	found["i0004"], _ = ticket.Parse(project)
	got := []any{}
	for _, id := range []string{"i0001", "i0001-01-01", "i0002", "i0003", "i0004"} {
		var e bytes.Buffer
		missing := MissingPlan(&e, found[id])
		notice := []string{}
		for _, line := range ChildlessNotice(conf, root, found[id]) {
			notice = append(notice, gitfixture.Rel(root, line))
		}
		got = append(got, []any{missing, e.String(), notice})
	}
	data, _ := json.Marshal(got)
	var norm []any
	if err := json.Unmarshal(data, &norm); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(norm, want) {
		t.Errorf("Python と違う\nGo:     %v\nPython: %v", norm, want)
	}
}
