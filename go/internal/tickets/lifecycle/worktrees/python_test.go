package worktrees

import (
	"bytes"
	"encoding/json"
	"path/filepath"
	"reflect"
	"testing"

	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/testkit"
)

// Python の worktrees と同じワークスペースを 2 つ作り、片付けの操作を同じ順に打って、出力・終了コード・残った
// ファイル・ワークツリー・ブランチが 1 字も違わないことを見る。

const merged = `merged_sha: "0123456789abcdef0123456789abcdef01234567"`
const completed = `completed_at: "2026-01-02T00:00:00+0900"`

// fixture のワークスペース。
//
//	i0001          親のワークツリー。承認済みチケット（作業中の親と子 1-03、閉じた子 1-01・1-02（取り消し）・2-01）
//	i0001-01-01    閉じた子。無視された生成物（node_modules・package.json の隣の out）と、退避するもの（.env・data/out）
//	i0001-01-02    閉じた子。未保存の下書き（未追跡）がある
//	i0001-01-03    作業中の子
//	i0001-02-01    閉じた子。何も無い
//	work-a         親のブランチから切った別の名前のワークツリー（親のチケットを持つ）
//	i0002          ロックしたワークツリー
//	plain          git のワークツリーでないディレクトリ
//	stale          git の登録が消えたディレクトリ（.git が消えた先を指す）
func fixture(t *testing.T) string {
	root := testkit.ShapedWorkspace(t)
	parent := testkit.AddWorktree(t, root, "i0001", "main")
	put := func(state, id, text string) { testkit.Put(t, testkit.Approved(root, "i0001", state, id), text) }
	put("doing", "i0001", testkit.ParentText("i0001", nil))
	put("doing", "i0001-01-03", testkit.ChildText("i0001-01-03", "i0001", "1", `started_at: "2026-01-01T00:00:00+0900"`))
	put("done", "i0001-01-01", testkit.ChildText("i0001-01-01", "i0001", "1", completed, merged))
	put("done", "i0001-01-02", testkit.ChildText("i0001-01-02", "i0001", "1", `cancelled_at: "2026-01-02T00:00:00+0900"`, `cancel_reason: "やめた"`))
	put("done", "i0001-02-01", testkit.ChildText("i0001-02-01", "i0001", "2", completed, merged))
	testkit.Commit(t, parent, "approve")
	for _, name := range []string{"i0001-01-01", "i0001-01-02", "i0001-01-03", "i0001-02-01", "work-a"} {
		testkit.AddWorktree(t, root, name, "i0001")
	}
	child := testkit.Worktree(root, "i0001-01-01")
	testkit.Put(t, filepath.Join(child, ".gitignore"), "node_modules/\n.env\nout/\n")
	testkit.Put(t, filepath.Join(child, "ext", "package.json"), "{}")
	testkit.Commit(t, child, "ignore")
	testkit.Put(t, filepath.Join(child, "node_modules", "a", "index.js"), "")
	testkit.Put(t, filepath.Join(child, "ext", "out", "e.js"), "")
	testkit.Put(t, filepath.Join(child, ".env"), "TOKEN=x\n")
	testkit.Put(t, filepath.Join(child, "data", "out", "keep.md"), "記録\n")
	testkit.Put(t, filepath.Join(testkit.Worktree(root, "i0001-01-02"), "wip", "handoff", "memo.md"), "メモ\n")
	testkit.Put(t, filepath.Join(testkit.Worktree(root, "work-a"), "wip", "handoff", "memo.md"), "メモ\n")
	testkit.AddWorktree(t, root, "i0002", "main")
	testkit.Git(t, root, "worktree", "lock", testkit.Worktree(root, "i0002"))
	testkit.Put(t, filepath.Join(testkit.Worktree(root, "plain"), "node_modules", "keep.js"), "")
	stale := testkit.Worktree(root, "stale")
	testkit.Put(t, filepath.Join(stale, ".git"), "gitdir: "+filepath.Join(root, ".git", "worktrees", "gone")+"\n")
	testkit.Put(t, filepath.Join(stale, "node_modules", "x.js"), "")
	testkit.Put(t, filepath.Join(stale, "src", "a.py"), "")
	return root
}

var plans = []string{
	testkit.ChildText("i0001-01-03", "i0001", "1"),
	testkit.ChildText("i0001-01-09", "i0001", "1"),
	testkit.ChildText("i0009-01-01", "i0009", "1"),
	testkit.ChildText("i0001-01-08", "i0001", "1", "project: p"),
	testkit.ParentText("i0003", nil),
}

const oracle = `
import io, json, os, sys
from ccnavi.infra import settings
from ccnavi.tickets.lifecycle import worktrees
from ccnavi.tickets.model import ticket as tm
c = json.load(sys.stdin)
root = c["root"]
def rel(s):
    return s.replace(root, "<root>")
conf, _ = settings.load(root)
out = {}
def run(name, f, *args):
    o, e = io.StringIO(), io.StringIO()
    code = f(o, e, *args)
    out[name] = [code, rel(o.getvalue()), rel(e.getvalue())]
out["targets"] = [[t.ticket for t in worktrees.tidy_targets(root, conf, "i0001", n)] for n in (None, 1, 2)]
out["family"] = [worktrees.family_for(root, conf, x) for x in ("i0001-01-01", "rel-2026-10-06", "i0001", "x", "rel-2026-10-06-01-02")]
out["children"] = worktrees.child_worktrees(root, [tm.parse(x)[0] for x in c["plans"]])
o = io.StringIO()
out["remaining"] = [worktrees.run_remaining(o, root, conf, "i0001-01-01", root), rel(o.getvalue())]
run("clean_preview", worktrees.run_clean, root, "i0001-01-01", True)
blocked = []
for name in ("i0001-01-01", "i0001-01-02", "nothing", "plain", "i0001", ".."):
    b = worktrees.blocked(root, name, os.path.join(root, ".claude", "worktrees", "i0001", "src"))
    blocked.append(None if b is None else [b.name, b.status, rel(b.path), rel(b.detail)])
out["blocked"] = blocked
for name in ("plain", "..", "a/b", "i0001-01-02", "i0002"):
    run("drop " + name, worktrees.run_drop, root, name, root)
run("drop i0001 inside", worktrees.run_drop, root, "i0001", os.path.join(root, ".claude", "worktrees", "i0001", "src"))
run("tidy", worktrees.run_tidy, root, conf, "i0001", None, root)
run("drop again", worktrees.run_drop, root, "i0001-01-01", root)
run("clean stale", worktrees.run_clean, root, "stale", False)
run("clean doing", worktrees.run_clean, root, "i0001-01-03", False)
run("clean bad", worktrees.run_clean, root, "a/b", False)
run("clean plain file", worktrees.run_clean, root, "nothing", False)
out["shape"] = shape(root)
json.dump(out, sys.stdout, ensure_ascii=False)
`

func TestMatchesPython(t *testing.T) {
	pyRoot, goRoot := fixture(t), fixture(t)
	want := testkit.Answers[map[string]any](t, "worktrees", testkit.ShapePy+oracle, map[string]any{"root": pyRoot, "plans": plans})
	root := goRoot
	conf := testkit.Settings(t, root)
	rel := func(s string) string { return testkit.Rel(root, s) }
	got := map[string]any{}
	run := func(name string, f func(stdout, stderr *bytes.Buffer) int) {
		var o, e bytes.Buffer
		code := f(&o, &e)
		got[name] = []any{code, rel(o.String()), rel(e.String())}
	}
	ids := func(ts []*ticket.Ticket) []string {
		out := []string{}
		for _, t := range ts {
			out = append(out, t.ID)
		}
		return out
	}
	one, two := 1, 2
	got["targets"] = []any{ids(TidyTargets(conf, root, "i0001", nil)), ids(TidyTargets(conf, root, "i0001", &one)), ids(TidyTargets(conf, root, "i0001", &two))}
	family := []string{}
	for _, x := range []string{"i0001-01-01", "rel-2026-10-06", "i0001", "x", "rel-2026-10-06-01-02"} {
		family = append(family, FamilyFor(conf, root, x))
	}
	got["family"] = family
	var parsed []*ticket.Ticket
	for _, text := range plans {
		p, _ := ticket.Parse(text)
		parsed = append(parsed, p)
	}
	children := []any{}
	for _, p := range ChildWorktrees(root, parsed) {
		children = append(children, plain(t, p.JSON()))
	}
	got["children"] = children
	var o bytes.Buffer
	got["remaining"] = []any{RunRemaining(&o, conf, root, "i0001-01-01"), rel(o.String())}
	run("clean_preview", func(o, e *bytes.Buffer) int { return RunClean(o, e, root, "i0001-01-01", true) })
	inside := filepath.Join(testkit.Worktree(root, "i0001"), "src")
	blocked := []any{}
	for _, name := range []string{"i0001-01-01", "i0001-01-02", "nothing", "plain", "i0001", ".."} {
		if b := Blocked(root, name, inside); b == nil {
			blocked = append(blocked, nil)
		} else {
			blocked = append(blocked, []any{b.Name, string(b.Status), rel(b.Path), rel(b.Detail)})
		}
	}
	got["blocked"] = blocked
	for _, name := range []string{"plain", "..", "a/b", "i0001-01-02", "i0002"} {
		run("drop "+name, func(o, e *bytes.Buffer) int { return RunDrop(o, root, name, root) })
	}
	run("drop i0001 inside", func(o, e *bytes.Buffer) int { return RunDrop(o, root, "i0001", inside) })
	run("tidy", func(o, e *bytes.Buffer) int { return RunTidy(o, conf, root, "i0001", nil, root) })
	run("drop again", func(o, e *bytes.Buffer) int { return RunDrop(o, root, "i0001-01-01", root) })
	run("clean stale", func(o, e *bytes.Buffer) int { return RunClean(o, e, root, "stale", false) })
	run("clean doing", func(o, e *bytes.Buffer) int { return RunClean(o, e, root, "i0001-01-03", false) })
	run("clean bad", func(o, e *bytes.Buffer) int { return RunClean(o, e, root, "a/b", false) })
	run("clean plain file", func(o, e *bytes.Buffer) int { return RunClean(o, e, root, "nothing", false) })
	got["shape"] = testkit.Shape(t, root)
	compare(t, got, want)
}

// plain は pyjson の値を encoding/json で比べられる形にする。
func plain(t *testing.T, v any) any {
	t.Helper()
	text, err := pyjson.Dumps(v, pyjson.Options{})
	if err != nil {
		t.Fatal(err)
	}
	var out any
	if err := json.Unmarshal([]byte(text), &out); err != nil {
		t.Fatal(err)
	}
	return out
}

func compare(t *testing.T, got, want map[string]any) {
	t.Helper()
	data, err := json.Marshal(got)
	if err != nil {
		t.Fatal(err)
	}
	var norm map[string]any
	if err := json.Unmarshal(data, &norm); err != nil {
		t.Fatal(err)
	}
	for key, w := range want {
		if g := norm[key]; !reflect.DeepEqual(g, w) {
			a, _ := json.MarshalIndent(g, "", " ")
			b, _ := json.MarshalIndent(w, "", " ")
			t.Errorf("%s が Python と違う\nGo:\n%s\nPython:\n%s", key, a, b)
		}
	}
	for key := range norm {
		if _, ok := want[key]; !ok {
			t.Errorf("%s が Python の答えに無い", key)
		}
	}
}
