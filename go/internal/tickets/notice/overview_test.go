package notice

import (
	"path/filepath"
	"testing"

	"ccnavi/internal/infra/golden"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/testkit"
)

// overviewWorkspace の形。
//
//	i0001  送ったあとに 1 コミット。子 01-01 は未着手で次の子（ワークツリーは合流済み）、02-01 は着手済みで
//	       未合流のコミットがある、02-02 は未コミットの変更がある、01-02 は先行の 02-01 が済んでいない
//	i0002  一度も送っていない、子が無い
//	i0003  提案（todo/）だけの親で、ワークツリーがある
//	i0004  承認済みだが、ワークツリーが無い（載せない）
func overviewWorkspace(t *testing.T) string {
	root := workspace(t)
	testkit.Remote(t, root)
	p1 := testkit.AddWorktree(t, root, "i0001", "main")
	testkit.Put(t, approvedPath(root, "i0001", "doing", "i0001"), parentText("i0001", []string{"  - work", "  - {type: research, after: [1]}"}, []string{"src/*", "wip/*"}))
	testkit.Put(t, approvedPath(root, "i0001", "doing", "i0001-01-01"), childText("i0001-01-01", "i0001", "1", []string{"src/*"}))
	testkit.Put(t, approvedPath(root, "i0001", "doing", "i0001-01-02"), childText("i0001-01-02", "i0001", "1", []string{"src/*"},
		"predecessors:\n  - i0001-02-01"))
	testkit.Put(t, approvedPath(root, "i0001", "doing", "i0001-02-01"), childText("i0001-02-01", "i0001", "2", []string{"wip/research/*"},
		`started_at: "2026-01-01T00:00:00+0900"`))
	testkit.Put(t, approvedPath(root, "i0001", "doing", "i0001-02-02"), childText("i0001-02-02", "i0001", "2", []string{"wip/research/*"},
		`started_at: "2026-01-01T00:00:00+0900"`))
	testkit.Commit(t, p1, "approve")
	testkit.Git(t, p1, "push", "-q", "origin", "i0001")
	testkit.AddWorktree(t, root, "i0001-01-01", "i0001")
	c2 := testkit.AddWorktree(t, root, "i0001-02-01", "i0001")
	testkit.Put(t, filepath.Join(c2, "wip", "research", "a.md"), "a\n")
	testkit.Commit(t, c2, "research")
	c3 := testkit.AddWorktree(t, root, "i0001-02-02", "i0001")
	testkit.Put(t, filepath.Join(c3, "wip", "research", "dirty.md"), "d\n")
	testkit.Put(t, filepath.Join(p1, "local.txt"), "l\n")
	testkit.Commit(t, p1, "local")
	p2 := testkit.AddWorktree(t, root, "i0002", "main")
	testkit.Put(t, approvedPath(root, "i0002", "doing", "i0002"), parentText("i0002", nil, []string{"src/*"}))
	testkit.Commit(t, p2, "approve")
	testkit.AddWorktree(t, root, "i0003", "main")
	testkit.Put(t, filepath.Join(root, "wip", "proposals", "todo", "i0003.md"), parentText("i0003", nil, []string{"src/*"}))
	p4 := filepath.Join(root, "elsewhere")
	testkit.Put(t, filepath.Join(p4, ".ccnavi", "approved", "doing", "i0004.md"), parentText("i0004", nil, []string{"src/*"}))
	return root
}

func TestOverviewMatchesPython(t *testing.T) {
	root := overviewWorkspace(t)
	want := golden.Load[map[string]any](t, "overview")
	conf := testkit.Settings(t, root)
	raw := approval.ReadRaw(conf, root)
	doing, _ := approval.Scan(conf, root, false, raw)
	closed, _ := approval.Scan(conf, root, true, raw)
	review, _ := approval.ScanReview(conf, root, raw)
	proposals := approval.ReadProposals(conf, root, raw).Kept
	var rows, lines, children, remotes []any
	for _, r := range Collect(conf, root, doing, review, closed, proposals) {
		rows = append(rows, []any{r.Ident, testkit.Rel(root, r.Path), r.Branch, r.MR, r.NextChild, r.Unsent, r.Project})
		lines = append(lines, Line(r))
		states := []any{}
		for _, c := range ChildStates(root, r, doing, review) {
			states = append(states, []any{c.Ident, c.State})
		}
		children = append(children, states)
		dumped, err := pyjson.Dumps(RemoteInfo(r.Path), pyjson.Options{})
		if err != nil {
			t.Fatal(err)
		}
		remote, _ := pyjson.Loads(dumped)
		remotes = append(remotes, plain(remote))
	}
	testkit.SameJSON(t, map[string]any{"rows": rows, "lines": lines, "children": children, "remote": remotes,
		"text": Parents(conf, root)}, want)
}

// plain は pyjson の値を encoding/json で出せる形にする。
func plain(v any) any {
	if o, ok := v.(*pyjson.Object); ok {
		out := map[string]any{}
		for k, x := range o.All() {
			out[k.(string)] = plain(x)
		}
		return out
	}
	return v
}
