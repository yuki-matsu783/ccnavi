package ops

import (
	"bytes"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/golden"
	"ccnavi/internal/tickets/lifecycle/consolidate"
	"ccnavi/internal/tickets/lifecycle/wapmerge"
	"ccnavi/internal/tickets/testkit"
)

// ワークスペースを作り、着手・判定の記録・合流の前の検査・終了・取り消し・統合の子の作成を同じ順に打って
// （合流とコミットは sh の代わりにテストが打つ）、出力・終了コード・書いたファイル・ワークツリー・ブランチが、
// 記録した答え（testdata/ops.json）と 1 字も違わないことを見る。

const stamp = "2026-01-06T00:00:00+0900"

const risks = `version: 1
levels:
  medium: 20
  high: 40
  critical: 70
factors:
  - id: untested
    points: 30
    judge: テストの無い振る舞いの変更を含むか
    message: テストの無い変更
  - id: hot
    points: 50
    glob: "src/hot/*"
    message: 重要なパス
`

const record1 = "---\nticket: i0001-01-01\nparent: i0001\nlearnings: 1\n---\n\n## 落とし穴\n\n- 種類: 未定\n- 内容: x\n"

func recordNone(id, parent string) string {
	return "---\nticket: " + id + "\nparent: " + parent + "\nlearnings: none\n---\n"
}

// fixture のワークスペース。
//
//	i0001  計画のある親。子 01-01（気づき 1 件を書く）と 01-02（取り消す）
//	i0002  計画のある親。子 01-01 は重要なパスを変え、実績のリスクでレビュー待ちに入る
//	i0003  提案（todo/）だけの親
//	i0004  計画のある親。子 01-01 は気づき無しで閉じ、親も閉じる
func fixture(t *testing.T) string {
	root := testkit.ShapedWorkspace(t)
	testkit.Put(t, filepath.Join(root, ".ccnavi", "config", "common", "risks.yml"), risks)
	for _, parent := range []string{"i0001", "i0002", "i0004"} {
		tree := testkit.AddWorktree(t, root, parent, "main")
		var extra []string
		if parent != "i0002" {
			extra = append(extra, "feedback: []")
		}
		testkit.Put(t, testkit.Approved(root, parent, "doing", parent), testkit.ParentText(parent, []string{"  - work"}, extra...))
		children := []string{parent + "-01-01"}
		if parent == "i0001" {
			children = append(children, "i0001-01-02")
		}
		for _, c := range children {
			testkit.Put(t, testkit.Approved(root, parent, "doing", c), testkit.ChildText(c, parent, "1"))
		}
		testkit.Commit(t, tree, "approve "+parent)
	}
	testkit.Put(t, filepath.Join(root, "wip", "proposals", "todo", "i0003.md"), testkit.ParentText("i0003", []string{"  - work"}))
	return root
}

func TestMatchesGolden(t *testing.T) {
	for _, kv := range append(testkit.GitEnv(), env.Learning+"=enable") {
		name, value, _ := strings.Cut(kv, "=")
		t.Setenv(name, value)
	}
	goRoot := fixture(t)
	want := golden.Load[map[string]any](t, "ops")

	root := goRoot
	restore, err := fsio.FixClock(stamp)
	if err != nil {
		t.Fatal(err)
	}
	defer restore()
	conf := testkit.Settings(t, root)
	rel := func(s string) string { return testkit.Rel(root, s) }
	wt := func(name string) string { return testkit.Worktree(root, name) }
	got := map[string]any{}
	run := func(name string, f func(o, e *bytes.Buffer) int) {
		var o, e bytes.Buffer
		code := f(&o, &e)
		got[name] = []any{code, rel(o.String()), rel(e.String())}
	}
	start := func(name, id string) {
		run(name, func(o, e *bytes.Buffer) int { return Start(o, e, conf, root, id) })
	}
	finish := func(name, id string) {
		run(name, func(o, e *bytes.Buffer) int { return Finish(o, e, conf, root, id) })
	}
	check := func(name string, verb Verb, id string) {
		run(name, func(o, e *bytes.Buffer) int { return Check(o, e, conf, root, verb, id, "") })
	}
	recordRisk := func(name, id, factor, answer, reason string) {
		run(name, func(o, e *bytes.Buffer) int { return RecordRisk(o, e, conf, root, id, factor, answer, reason) })
	}
	cancel := func(name, id, reason string) {
		run(name, func(o, e *bytes.Buffer) int { return Cancel(o, e, conf, root, id, reason, true) })
	}
	cons := func(name, id string, f func(o, e io.Writer, conf *config.Settings, root, id string) int) {
		run(name, func(o, e *bytes.Buffer) int { return f(o, e, conf, root, id) })
	}
	wap := func(name, id string) {
		var o bytes.Buffer
		code := wapmerge.Plan(&o, conf, root, id)
		got[name] = []any{code, rel(o.String())}
	}
	merge := func(parent, child string) {
		testkit.Git(t, wt(parent), "merge", "--no-ff", "--no-commit", testkit.Head(t, wt(child)))
	}
	const c1, c2, c3, c4 = "i0001-01-01", "i0001-01-02", "i0002-01-01", "i0004-01-01"

	check("check start parent", verbStart, "i0001")
	start("start unknown", "i0099")
	start("start child early", c1)
	start("start parent", "i0001")
	start("start parent again", "i0001")
	testkit.Commit(t, wt("i0001"), "start")
	testkit.Git(t, root, "worktree", "add", "-q", "-b", c1, filepath.Join(".claude", "worktrees", c1), "i0001")
	start("start c1", c1)
	testkit.Put(t, filepath.Join(wt(c1), "src", "a.py"), "a = 1\n")
	testkit.Put(t, filepath.Join(wt(c1), "wip", "handoff", "learnings", c1+".md"), record1)
	testkit.Commit(t, wt(c1), "work c1")
	check("check finish c1", verbFinish, c1)
	recordRisk("risk bad answer", c1, "untested", "maybe", "r")
	recordRisk("risk no reason", c1, "untested", "yes", "  ")
	recordRisk("risk parent", "i0001", "untested", "yes", "r")
	recordRisk("risk not judge", c1, "hot", "yes", "r")
	wap("wap parent", "i0001")
	finish("finish c1 pending", c1)
	recordRisk("risk c1", c1, "untested", " NO ", "テストを足した")
	wap("wap c1", c1)
	merge("i0001", c1)
	finish("finish c1", c1)
	testkit.Commit(t, wt("i0001"), "finish c1")
	check("check cancel c2", verbCancel, c2)
	cancel("cancel c2 no reason", c2, " ")
	cancel("cancel todo", "i0003", "r")
	cancel("cancel c2", c2, " やめた ")
	testkit.Commit(t, wt("i0001"), "cancel c2")
	finish("finish parent learning", "i0001")
	cons("check consolidate", "i0001", consolidate.Check)
	cons("consolidate child", c1, consolidate.Run)
	cons("consolidate", "i0001", consolidate.Run)
	cons("consolidate again", "i0001", consolidate.Run)
	cons("check consolidate absent", "i0098", consolidate.Check)
	start("start parent 2", "i0002")
	testkit.Commit(t, wt("i0002"), "start")
	testkit.Git(t, root, "worktree", "add", "-q", "-b", c3, filepath.Join(".claude", "worktrees", c3), "i0002")
	wap("wap c3 not started", c3)
	start("start c3", c3)
	testkit.Put(t, filepath.Join(wt(c3), "src", "hot", "x.py"), "x = 1\n")
	wap("wap c3 dirty", c3)
	testkit.Put(t, filepath.Join(wt(c3), "wip", "handoff", "learnings", c3+".md"), recordNone(c3, "i0002"))
	testkit.Commit(t, wt(c3), "work c3")
	testkit.Put(t, filepath.Join(wt(c3), "docs", "out.md"), "out\n")
	testkit.Commit(t, wt(c3), "outside")
	wap("wap c3 outside", c3)
	testkit.Git(t, wt(c3), "rm", "-q", "docs/out.md")
	testkit.Commit(t, wt(c3), "back")
	testkit.Put(t, filepath.Join(wt(c3), "src", "cache.txt"), "child\n")
	testkit.Git(t, wt(c3), "add", "-f", "src/cache.txt")
	testkit.Commit(t, wt(c3), "cache")
	exclude := strings.TrimSpace(testkit.Git(t, wt("i0002"), "rev-parse", "--git-path", "info/exclude"))
	if !filepath.IsAbs(exclude) {
		exclude = filepath.Join(wt("i0002"), exclude)
	}
	testkit.Put(t, exclude, "src/cache.txt\n")
	testkit.Put(t, filepath.Join(wt("i0002"), "src", "cache.txt"), "parent\n")
	wap("wap c3 ignored", c3)
	os.Remove(filepath.Join(wt("i0002"), "src", "cache.txt"))
	recordRisk("risk c3", c3, "untested", "yes", "テストが無い")
	wap("wap c3", c3)
	merge("i0002", c3)
	finish("finish c3", c3)
	testkit.Commit(t, wt("i0002"), "finish c3")
	finish("finish parent 2", "i0002")
	check("check finish parent 2", verbFinish, "i0002")
	start("start parent 4", "i0004")
	testkit.Commit(t, wt("i0004"), "start")
	testkit.Git(t, root, "worktree", "add", "-q", "-b", c4, filepath.Join(".claude", "worktrees", c4), "i0004")
	start("start c4", c4)
	testkit.Put(t, filepath.Join(wt(c4), "src", "b.py"), "b = 1\n")
	testkit.Put(t, filepath.Join(wt(c4), "wip", "handoff", "learnings", c4+".md"), recordNone(c4, "i0004"))
	testkit.Commit(t, wt(c4), "work c4")
	recordRisk("risk c4", c4, "untested", "no", "r")
	wap("wap c4", c4)
	merge("i0004", c4)
	finish("finish c4", c4)
	testkit.Commit(t, wt("i0004"), "finish c4")
	check("check finish parent 4", verbFinish, "i0004")
	finish("finish parent 4", "i0004")
	finish("finish parent 4 again", "i0004")
	got["texts"] = fileTexts(t, root)
	got["shape"] = testkit.Shape(t, root)
	compare(t, got, want)
}

// fileTexts は承認済みの置き場・wip/・判定の問いのファイルの中身（root は <root>）。
func fileTexts(t *testing.T, root string) map[string]any {
	t.Helper()
	found := map[string]any{}
	err := filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() {
			if d.Name() == ".git" {
				return filepath.SkipDir
			}
			return nil
		}
		r, _ := filepath.Rel(root, path)
		r = filepath.ToSlash(r)
		if strings.Contains("/"+r, "/.ccnavi/approved/") || strings.HasPrefix(r, "wip/") || strings.Contains(r, "/wip/") ||
			strings.HasPrefix(r, "logs/state/risk-judge-") {
			data, err := os.ReadFile(path)
			if err != nil {
				return err
			}
			found[r] = testkit.Rel(root, string(data))
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return found
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
	keys := make([]string, 0, len(want))
	for key := range want {
		keys = append(keys, key)
	}
	slices.Sort(keys)
	for _, key := range keys {
		if g := norm[key]; !reflect.DeepEqual(g, want[key]) {
			a, _ := json.MarshalIndent(g, "", " ")
			b, _ := json.MarshalIndent(want[key], "", " ")
			t.Errorf("%s が golden と違う\nGo:\n%s\ngolden:\n%s", key, a, b)
		}
	}
	for key := range norm {
		if _, ok := want[key]; !ok {
			t.Errorf("%s が golden の答えに無い", key)
		}
	}
}
