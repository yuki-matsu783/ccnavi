package closing

import (
	"bytes"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/golden"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/testkit"
)

// ワークスペースを読ませ、引き方・止める文・閉じられない理由・成果物の検査・合流されていない修正が、
// 記録した答え（testdata/closing.json）と 1 字も違わないことを見る。

var closingIDs = []string{"i0001", "i0001-01-01", "i0001-02-01", "i0002", "i0002-01-01", "i0003", "i0003-01-01",
	"i0005", "i0006", "i0006-01-01", "i0007", "i0007-01-01", "i0008", "i0008-01-01", "i0009-01-01", "i0010", "i0099"}

// closingWorkspace の形。
//
//	i0001 着手済みの親（調査と設計）。子 01-01 は作業中、子 02-01 は未着手で先行に無い識別子を挙げる
//	i0002 未着手の親と、作業中の子
//	i0003 親は提案（todo/）だけ。子は承認済み
//	i0005 親のワークツリーが無く、ワークスペースルートと別のワークツリーにコピーがある
//	i0006 フィードバック計画まで済んだ親。成果物を追跡し、子のワークツリーに finish の後の修正がある
//	i0007 ユーザが早めに閉じた親
//	i0008 レビューの要るフェーズが閉じて止まっている親
//	i0009 閉じた親と、作業中の子
//	i0010 親のフォルダが別のブランチを開いている。そこの早めに閉じたマーカーは読まない
func closingWorkspace(t *testing.T) string {
	root := clock.Workspace(t, "i0001", "i0002", "i0003", "i0006", "i0007", "i0008", "i0009", "i0010", "w5")
	write := func(treeRoot, state, id, text string) {
		testkit.Put(t, filepath.Join(approvedDir(treeRoot), state, id+".md"), text)
	}
	research, design := "  - research", "  - {type: design, after: [1]}"
	w := func(name string) string { return testkit.Worktree(root, name) }

	write(w("i0001"), "doing", "i0001", testkit.ClosingParent("i0001", []string{testkit.ClosingStarted}, research, design))
	write(w("i0001"), "doing", "i0001-01-01", testkit.ClosingChild("i0001-01-01", "i0001", "1", testkit.ClosingStarted))
	write(w("i0001"), "doing", "i0001-02-01", testkit.ClosingChild("i0001-02-01", "i0001", "2", "predecessors: [i0001-01-01, i0098]"))

	write(w("i0002"), "doing", "i0002", testkit.ClosingParent("i0002", nil, research))
	write(w("i0002"), "doing", "i0002-01-01", testkit.ClosingChild("i0002-01-01", "i0002", "1", testkit.ClosingStarted))

	testkit.Put(t, filepath.Join(root, "wip", "proposals", "todo", "i0003.md"), testkit.ClosingParent("i0003", nil, research))
	write(w("i0003"), "doing", "i0003-01-01", testkit.ClosingChild("i0003-01-01", "i0003", "1"))

	write(root, "doing", "i0005", testkit.ClosingParent("i0005", []string{testkit.ClosingStarted}, research))
	write(w("w5"), "doing", "i0005", testkit.ClosingParent("i0005", []string{testkit.ClosingStarted}, research))

	write(w("i0006"), "doing", "i0006", testkit.ClosingParent("i0006", []string{testkit.ClosingStarted, "feedback: []"}, research))
	write(w("i0006"), "done", "i0006-01-01", testkit.ClosingChild("i0006-01-01", "i0006", "1", testkit.ClosingStarted, testkit.ClosingDone))
	testkit.Put(t, filepath.Join(w("i0006"), "wip", "research", "summary.md"), "まとめ\n")
	commitAll(t, w("i0006"), "research")
	child := testkit.AddWorktree(t, root, "i0006-01-01", "i0006")
	testkit.Put(t, filepath.Join(child, "src", "fix.txt"), "fix\n")
	commitAll(t, child, "late fix")

	write(w("i0007"), "doing", "i0007", testkit.ClosingParent("i0007", []string{testkit.ClosingStarted}, research))
	write(w("i0007"), "done", "i0007-01-01", testkit.ClosingChild("i0007-01-01", "i0007", "1", testkit.ClosingStarted, testkit.ClosingDone))
	testkit.Put(t, filepath.Join(approvedDir(w("i0007")), "phases", "i0007", "close-early.json"), `{"at": "2026-01-02"}`)

	write(w("i0008"), "doing", "i0008", testkit.ClosingParent("i0008", []string{testkit.ClosingStarted}, "  - design"))
	write(w("i0008"), "done", "i0008-01-01", testkit.ClosingChild("i0008-01-01", "i0008", "1", testkit.ClosingStarted, testkit.ClosingDone))

	write(w("i0009"), "done", "i0009", testkit.ClosingParent("i0009", []string{testkit.ClosingStarted, testkit.ClosingDone}, research))
	write(w("i0009"), "doing", "i0009-01-01", testkit.ClosingChild("i0009-01-01", "i0009", "1"))

	clock.Git(t, w("i0010"), "checkout", "-q", "-b", "other")
	write(w("i0010"), "doing", "i0010", testkit.ClosingParent("i0010", []string{testkit.ClosingStarted}, research))
	write(w("i0010"), "done", "i0010-01-01", testkit.ClosingChild("i0010-01-01", "i0010", "1", testkit.ClosingStarted, testkit.ClosingDone))
	testkit.Put(t, filepath.Join(approvedDir(w("i0010")), "phases", "i0010", "close-early.json"), `{"at": "2026-01-02"}`)
	return root
}

func list[T any](xs []T) []T {
	if xs == nil {
		return []T{}
	}
	return xs
}

func TestMatchesGolden(t *testing.T) {
	goRoot := closingWorkspace(t)
	want := golden.Load[map[string]any](t, "closing")
	conf := testkit.Settings(t, goRoot)
	rel := func(s string) string { return strings.ReplaceAll(s, goRoot, "<root>") }
	got := map[string]any{}
	for _, id := range closingIDs {
		var buf bytes.Buffer
		f := Find(&buf, conf, goRoot, id)
		var shown any
		if f != nil {
			shown = []any{f.ID, f.State, f.Tree}
		}
		problems := list(CloseProblems(conf, goRoot, id, nil))
		for i, p := range problems {
			problems[i] = rel(p)
		}
		e := map[string]any{"find": rel(buf.String()), "found": shown, "absent": Absent(conf, goRoot, id), "close": problems}
		if f != nil {
			for name, fn := range map[string]func(*bytes.Buffer) bool{
				"misplaced":  func(b *bytes.Buffer) bool { return MisplacedHome(b, conf, goRoot, f) },
				"stopped":    func(b *bytes.Buffer) bool { return FamilyStopped(b, conf, goRoot, f) },
				"notStarted": func(b *bytes.Buffer) bool { return ParentNotStarted(b, conf, goRoot, f) },
				"unmet":      func(b *bytes.Buffer) bool { return PredecessorsUnmet(b, conf, goRoot, f) },
				"busy":       func(b *bytes.Buffer) bool { return ParentStillBusy(b, conf, goRoot, f) },
				"missing":    func(b *bytes.Buffer) bool { return DeliverablesMissing(b, conf, goRoot, f) },
			} {
				var b bytes.Buffer
				r := fn(&b)
				e[name] = []any{r, rel(b.String())}
			}
			e["inWip"] = list(DeliverablesInWip(conf, goRoot, f))
			e["type"] = ""
			if pt := PhaseType(conf, goRoot, f); pt != nil {
				e["type"] = pt.ID
			}
			home := f.Parent
			if home == "" {
				home = f.ID
			}
			e["fix"] = UnmergedFix(goRoot, tree.WorktreePath(goRoot, home), f, git.DefaultTimeout)
		}
		got[id] = e
	}
	testkit.SameJSON(t, got, want)
	// 決まらない親のフォルダのマーカーで、閉じてよいことにしない。
	if len(CloseProblems(conf, goRoot, "i0010", nil)) == 0 {
		t.Error("決まらない親のフォルダの close-early を読んだ")
	}
}
