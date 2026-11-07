package retire

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/golden"
	"ccnavi/internal/tickets/testkit"
)

// ワークスペースを作り、片付けの記録・親を閉じた後の片付け・残したブランチの消し直し・MR のマージの後の片付け・
// 放棄を同じ順に打って、出力・終了コード・記録・残ったワークツリーとブランチが、記録した答え（testdata/retire.json）と
// 1 字も違わないことを見る。

// fixture のワークスペース。親 i0001 は origin に送ってある。
//
//	i0001-01-01  合流した子（merged_sha は合流した先頭）
//	i0001-01-02  取り消した子（親に入っていないコミットがある）
//	i0001-01-03  合流した子だが、合流のあとに進んだ
//	i0001-02-01  合流した子。ワークツリーに無視された .env がある
func fixture(t *testing.T) (string, string) {
	root := testkit.ShapedWorkspace(t)
	testkit.Remote(t, root)
	parent := testkit.AddWorktree(t, root, "i0001", "main")
	shas := map[string]string{}
	for _, name := range []string{"i0001-01-01", "i0001-01-02", "i0001-01-03", "i0001-02-01"} {
		child := testkit.AddWorktree(t, root, name, "i0001")
		testkit.Put(t, filepath.Join(child, "src", name+".py"), "x = 1\n")
		testkit.Commit(t, child, "work "+name)
		if name != "i0001-01-02" {
			testkit.Git(t, parent, "merge", "-q", "--no-ff", "-m", "merge "+name, name)
			shas[name] = testkit.Head(t, child)
		}
	}
	testkit.Commit(t, testkit.Worktree(root, "i0001-01-03"), "合流のあとの変更")
	exclude := testkit.Git(t, testkit.Worktree(root, "i0001-02-01"), "rev-parse", "--git-path", "info/exclude")
	excludePath := filepath.Clean(exclude[:len(exclude)-1])
	if !filepath.IsAbs(excludePath) {
		excludePath = filepath.Join(testkit.Worktree(root, "i0001-02-01"), excludePath)
	}
	testkit.Put(t, excludePath, ".env\n")
	testkit.Put(t, filepath.Join(testkit.Worktree(root, "i0001-02-01"), ".env"), "SECRET=1\n")
	put := func(state, id, text string) { testkit.Put(t, testkit.Approved(root, "i0001", state, id), text) }
	done := `completed_at: "2026-01-02T00:00:00+0900"`
	put("doing", "i0001", testkit.ParentText("i0001", nil))
	for _, name := range []string{"i0001-01-01", "i0001-01-03", "i0001-02-01"} {
		phase := name[len(name)-4 : len(name)-3]
		put("done", name, testkit.ChildText(name, "i0001", phase, done, `merged_sha: "`+shas[name]+`"`))
	}
	put("done", "i0001-01-02", testkit.ChildText("i0001-01-02", "i0001", "1", `cancelled_at: "2026-01-02T00:00:00+0900"`))
	testkit.Commit(t, parent, "close")
	testkit.Git(t, parent, "push", "-q", "origin", "i0001")
	return root, testkit.Head(t, parent)
}

func TestMatchesGolden(t *testing.T) {
	goRoot, head := fixture(t)
	want := golden.Load[map[string]any](t, "retire")
	root := goRoot
	conf := testkit.Settings(t, root)
	rel := func(s string) string { return testkit.Rel(root, s) }
	parent := testkit.Worktree(root, "i0001")
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
	memo := testkit.Put(t, filepath.Join(parent, "memo.txt"), "書きかけ\n")
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
	got["after retire"] = testkit.Shape(t, root)
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
		testkit.Put(t, filepath.Join(filepath.Dir(record), name), text)
	}
	got["entries broken"] = entries()
	run("retire broken", func(o, e *bytes.Buffer) int { return RunRetire(o, e, conf, root, "i0005", root) })
	got["note broken"] = rel(Note(conf, root, "i0005", "", "", nil))
	got["shape"] = testkit.Shape(t, root)
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
			t.Errorf("%s が golden と違う\nGo:\n%s\ngolden:\n%s", key, a, b)
		}
	}
	for key := range norm {
		if _, ok := want[key]; !ok {
			t.Errorf("%s が golden の答えに無い", key)
		}
	}
}
