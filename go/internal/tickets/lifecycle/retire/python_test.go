package retire

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/lifecycle/internal/gitfixture"
	"ccnavi/internal/tickets/model/pyoracle"
)

// Python の retire と同じワークスペースを 2 つ作り、片付けの記録・親を閉じた後の片付け・残したブランチの消し直し・
// MR のマージの後の片付け・放棄を同じ順に打って、出力・終了コード・記録・残ったワークツリーとブランチが 1 字も
// 違わないことを見る。

// fixture のワークスペース。親 i0001 は origin に送ってある。
//
//	i0001-01-01  合流した子（merged_sha は合流した先頭）
//	i0001-01-02  取り消した子（親に入っていないコミットがある）
//	i0001-01-03  合流した子だが、合流のあとに進んだ
//	i0001-02-01  合流した子。ワークツリーに無視された .env がある
func fixture(t *testing.T) (string, string) {
	root := gitfixture.Workspace(t)
	gitfixture.Remote(t, root)
	parent := gitfixture.AddWorktree(t, root, "i0001", "main")
	shas := map[string]string{}
	for _, name := range []string{"i0001-01-01", "i0001-01-02", "i0001-01-03", "i0001-02-01"} {
		child := gitfixture.AddWorktree(t, root, name, "i0001")
		gitfixture.Put(t, filepath.Join(child, "src", name+".py"), "x = 1\n")
		gitfixture.Commit(t, child, "work "+name)
		if name != "i0001-01-02" {
			gitfixture.Git(t, parent, "merge", "-q", "--no-ff", "-m", "merge "+name, name)
			shas[name] = gitfixture.Head(t, child)
		}
	}
	gitfixture.Commit(t, gitfixture.Worktree(root, "i0001-01-03"), "合流のあとの変更")
	exclude := gitfixture.Git(t, gitfixture.Worktree(root, "i0001-02-01"), "rev-parse", "--git-path", "info/exclude")
	excludePath := filepath.Clean(exclude[:len(exclude)-1])
	if !filepath.IsAbs(excludePath) {
		excludePath = filepath.Join(gitfixture.Worktree(root, "i0001-02-01"), excludePath)
	}
	gitfixture.Put(t, excludePath, ".env\n")
	gitfixture.Put(t, filepath.Join(gitfixture.Worktree(root, "i0001-02-01"), ".env"), "SECRET=1\n")
	put := func(state, id, text string) { gitfixture.Put(t, gitfixture.Approved(root, "i0001", state, id), text) }
	done := `completed_at: "2026-01-02T00:00:00+0900"`
	put("doing", "i0001", gitfixture.ParentText("i0001", nil))
	for _, name := range []string{"i0001-01-01", "i0001-01-03", "i0001-02-01"} {
		phase := name[len(name)-4 : len(name)-3]
		put("done", name, gitfixture.ChildText(name, "i0001", phase, done, `merged_sha: "`+shas[name]+`"`))
	}
	put("done", "i0001-01-02", gitfixture.ChildText("i0001-01-02", "i0001", "1", `cancelled_at: "2026-01-02T00:00:00+0900"`))
	gitfixture.Commit(t, parent, "close")
	gitfixture.Git(t, parent, "push", "-q", "origin", "i0001")
	return root, gitfixture.Head(t, parent)
}

const oracle = `
import io, json, os, sys
from ccnavi.infra import settings
from ccnavi.tickets.lifecycle import retire
c = json.load(sys.stdin)
root = c["root"]
def rel(s):
    return s.replace(root, "<root>")
conf, _ = settings.load(root)
parent = os.path.join(root, ".claude", "worktrees", "i0001")
record = os.path.join(conf.state, "retire", "i0001.json")
out = {}
def run(name, f, *args):
    o, e = io.StringIO(), io.StringIO()
    code = f(o, e, *args)
    out[name] = [code, rel(o.getvalue()), rel(e.getvalue())]
def read_record():
    try:
        with open(record, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None
out["note"] = [retire.note(root, conf, "i0001", "", parent, 7), read_record()]
out["note again"] = [retire.note(root, conf, "i0001", "", parent, None), read_record()]
out["entries"] = [[e.parent, e.repo, e.mr, e.branch, e.incomplete] for e in retire.entries(conf)]
run("branch early ask", retire.run_branch, root, conf, "i0001-01-02", False)
run("branch early confirmed", retire.run_branch, root, conf, "i0001-01-02", True)
memo = os.path.join(parent, "memo.txt")
with open(memo, "w", encoding="utf-8") as f:
    f.write("書きかけ\n")
run("retire blocked", retire.run_retire, root, conf, "i0001", root)
os.remove(memo)
run("retire inside", retire.run_retire, root, conf, "i0001", os.path.join(parent, "src"))
run("retire", retire.run_retire, root, conf, "i0001", root)
run("retire again", retire.run_retire, root, conf, "i0001", root)
run("branch unknown", retire.run_branch, root, conf, "main", False)
run("branch ask", retire.run_branch, root, conf, "i0001-01-02", False)
run("branch moved", retire.run_branch, root, conf, "I0001-01-03", False)
out["after retire"] = shape(root)
run("merged", retire.run_merged, root, conf, "i0001", "", "i0001", c["head"], root)
out["record"] = read_record()
run("merged again", retire.run_merged, root, conf, "i0001", "", "i0001", c["head"], root)
run("abandon", retire.run_abandon, root, conf, "i0001")
run("abandon again", retire.run_abandon, root, conf, "i0001")
os.makedirs(os.path.dirname(record), exist_ok=True)
for name, text in (("i0005.json", "{"), ("i0006.json", '{"parent": "i0006", "children": {}}'),
                   ("i0007.json", '{"parent": "i0007", "mr": true}'), ("i0008.json", '{"parent": "x"}'),
                   ("i0009.json", '{"parent": "I0009", "mr": " 0012 ", "branch": "a b"}'),
                   ("i0010.json", '{"parent": "i0010", "mr": 3, "branch": "b", "project": "p"}'),
                   ("i0011.tmp.json", "{}"), ("x y.json", "{}")):
    with open(os.path.join(os.path.dirname(record), name), "w", encoding="utf-8") as f:
        f.write(text)
out["entries broken"] = [[e.parent, e.repo, e.mr, e.branch, e.incomplete] for e in retire.entries(conf)]
run("retire broken", retire.run_retire, root, conf, "i0005", root)
out["note broken"] = rel(retire.note(root, conf, "i0005", "", "", None))
out["shape"] = shape(root)
json.dump(out, sys.stdout, ensure_ascii=False)
`

func TestMatchesPython(t *testing.T) {
	pyRoot, head := fixture(t)
	goRoot, goHead := fixture(t)
	if head != goHead {
		t.Fatalf("2 つのワークスペースの sha が違う: %s %s", head, goHead)
	}
	want := pyoracle.Answers[map[string]any](t, "retire", gitfixture.ShapePy+oracle, map[string]any{"root": pyRoot, "head": head})
	root := goRoot
	conf := gitfixture.Settings(t, root)
	rel := func(s string) string { return gitfixture.Rel(root, s) }
	parent := gitfixture.Worktree(root, "i0001")
	record := filepath.Join(conf.State, "retire", "i0001.json")
	got := map[string]any{}
	run := func(name string, f func(o, e *bytes.Buffer) int) {
		var o, e bytes.Buffer
		code := f(&o, &e)
		got[name] = []any{code, rel(o.String()), rel(e.String())}
	}
	readRecord := func() any {
		data, err := os.ReadFile(record)
		if err != nil {
			return nil
		}
		return string(data)
	}
	entries := func() []any {
		out := []any{}
		for _, e := range Entries(conf) {
			out = append(out, []any{e.Parent, e.Repo, e.MR, e.Branch, e.Incomplete})
		}
		return out
	}
	got["note"] = []any{Note(conf, root, "i0001", "", parent, 7), readRecord()}
	got["note again"] = []any{Note(conf, root, "i0001", "", parent, nil), readRecord()}
	got["entries"] = entries()
	run("branch early ask", func(o, e *bytes.Buffer) int { return RunBranch(o, e, conf, root, "i0001-01-02", false) })
	run("branch early confirmed", func(o, e *bytes.Buffer) int { return RunBranch(o, e, conf, root, "i0001-01-02", true) })
	memo := gitfixture.Put(t, filepath.Join(parent, "memo.txt"), "書きかけ\n")
	run("retire blocked", func(o, e *bytes.Buffer) int { return RunRetire(o, e, conf, root, "i0001", root) })
	os.Remove(memo)
	run("retire inside", func(o, e *bytes.Buffer) int {
		return RunRetire(o, e, conf, root, "i0001", filepath.Join(parent, "src"))
	})
	run("retire", func(o, e *bytes.Buffer) int { return RunRetire(o, e, conf, root, "i0001", root) })
	run("retire again", func(o, e *bytes.Buffer) int { return RunRetire(o, e, conf, root, "i0001", root) })
	run("branch unknown", func(o, e *bytes.Buffer) int { return RunBranch(o, e, conf, root, "main", false) })
	run("branch ask", func(o, e *bytes.Buffer) int { return RunBranch(o, e, conf, root, "i0001-01-02", false) })
	run("branch moved", func(o, e *bytes.Buffer) int { return RunBranch(o, e, conf, root, "I0001-01-03", false) })
	got["after retire"] = gitfixture.Shape(t, root)
	run("merged", func(o, e *bytes.Buffer) int { return RunMerged(o, conf, root, "i0001", "", "i0001", head, root) })
	got["record"] = readRecord()
	run("merged again", func(o, e *bytes.Buffer) int { return RunMerged(o, conf, root, "i0001", "", "i0001", head, root) })
	run("abandon", func(o, e *bytes.Buffer) int { return RunAbandon(o, e, conf, root, "i0001") })
	run("abandon again", func(o, e *bytes.Buffer) int { return RunAbandon(o, e, conf, root, "i0001") })
	for name, text := range map[string]string{
		"i0005.json": "{", "i0006.json": `{"parent": "i0006", "children": {}}`, "i0007.json": `{"parent": "i0007", "mr": true}`,
		"i0008.json": `{"parent": "x"}`, "i0009.json": `{"parent": "I0009", "mr": " 0012 ", "branch": "a b"}`,
		"i0010.json": `{"parent": "i0010", "mr": 3, "branch": "b", "project": "p"}`, "i0011.tmp.json": "{}", "x y.json": "{}",
	} {
		gitfixture.Put(t, filepath.Join(filepath.Dir(record), name), text)
	}
	got["entries broken"] = entries()
	run("retire broken", func(o, e *bytes.Buffer) int { return RunRetire(o, e, conf, root, "i0005", root) })
	got["note broken"] = rel(Note(conf, root, "i0005", "", "", nil))
	got["shape"] = gitfixture.Shape(t, root)
	compare(t, got, want)
}

func TestNoStateDirectory(t *testing.T) {
	conf := &config.Settings{}
	if got := Note(conf, t.TempDir(), "i0001", "", "", nil); got != "state の置き場が空なので、片付けの記録を書けない" {
		t.Errorf("%q", got)
	}
	if got := Entries(conf); got != nil {
		t.Errorf("%v", got)
	}
}

func TestMRText(t *testing.T) {
	for _, c := range []struct {
		in   any
		want string
	}{{7, "7"}, {" 12 ", "12"}, {"0", ""}, {true, ""}, {nil, ""}, {3.0, ""}, {"١٢", ""}, {"-1", ""}} {
		if got := mrText(c.in); got != c.want {
			t.Errorf("%v: %q", c.in, got)
		}
	}
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
