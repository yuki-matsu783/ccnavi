package shask

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/golden"
	"ccnavi/internal/tickets/testkit"
)

// ワークスペースに問いを投げ、答えの行・標準エラー・終了コードが見本（testdata）と 1 字も違わないことを見る。sh は行の形をそのまま読むので、ここが sh との契約の見張りになる。

func parentFront(id string, extra ...string) string {
	return "---\nversion: 1\nticket: " + id + "\n" + strings.Join(extra, "\n") + "\ntitle: 親\n---\n\n本文\n"
}

// shaskWorkspace は問いの答えが分かれるワークスペース。i0001 は承認済みの親（着手済み・MR・頼んだ印・作業中の子・改版
// の提案）、i0002 は読めない親、i0003 は手元の退避にだけ在る親、i0004 はレビュー待ちの提案だけの親、fix-a は直接作業
// （取り込んでいない下書きと、元と中身の違う写し）。
func shaskWorkspace(t *testing.T) string {
	root := testkit.Workspace(t, "i0001", "i0002", "i0003", "i0004", "fix-a")
	w := func(name string, rel ...string) string {
		return filepath.Join(append([]string{testkit.Worktree(root, name)}, rel...)...)
	}
	testkit.Put(t, w("i0001", ".ccnavi", "approved", "doing", "i0001.md"),
		parentFront("i0001", "mr: 7", `started_at: "2026-01-01T00:00:00+0900"`))
	testkit.Put(t, w("i0001", ".ccnavi", "approved", "doing", "i0001-01-01.md"), testkit.ChildText("i0001-01-01", "i0001", "1"))
	testkit.Put(t, w("i0001", ".ccnavi", "approved", "doing", "i0001-01-02.md"), testkit.ChildText("i0001-01-02", "I0001", "1"))
	testkit.Put(t, w("i0001", ".ccnavi", "approved", "doing", "broken.md"), "---\nparent: [\n---\n")
	testkit.Put(t, w("i0001", ".ccnavi", "approved", "phases", "i0001", "1.requested"), "{}")
	testkit.Put(t, w("i0001", "wip", "proposals", "todo", "i0001.md"), parentFront("i0001", "mr: 7"))
	testkit.Put(t, w("i0002", ".ccnavi", "approved", "doing", "i0002.md"), "---\nticket: [\n---\n")
	testkit.Put(t, w("i0002", ".ccnavi", "approved", "phases", "i0002", "x1.requested"), "{}")
	testkit.Put(t, filepath.Join(root, "logs", "archive", "self", "done", "i0003.md"), parentFront("i0003", "mr: 42"))
	testkit.Put(t, w("i0004", "wip", "proposals", "review", "i0004.md"), parentFront("i0004", `mr: "１２"`))
	testkit.Put(t, w("i0004", "wip", "proposals", "todo", "i0004.md"), parentFront("i0004", "mr: true", "parent: i0009"))
	testkit.Put(t, w("fix-a", "README.md"), "元\n")
	testkit.Commit(t, w("fix-a"), "readme")
	testkit.Put(t, w("fix-a", "wip", "handoff", "README.md"), "書き換え\n")
	testkit.Put(t, w("fix-a", "wip", "handoff", "memo.md"), "メモ\n")
	return root
}

// shapeCase は親の見分け方を、ファイルの形ごとに 1 つずつ見る場面。親のワークツリーは名前だけで、置くのは files（ワークツリー
// からの相対。"archive/" で始まるものはワークスペースの logs/archive/self/done に置く）。
type shapeCase struct {
	name  string
	files map[string]string
}

const approvedDoing = ".ccnavi/approved/doing/"

func front(fields ...string) string {
	return "---\n" + strings.Join(fields, "\n") + "\n---\n本文\n"
}

var shapeCases = []shapeCase{
	{"s-done", map[string]string{".ccnavi/approved/done/s-done.md": front("ticket: s-done")}},
	{"s-review", map[string]string{"wip/proposals/review/s-review.md": front("ticket: s-review")}},
	{"s-child", map[string]string{approvedDoing + "s-child.md": front("ticket: s-child", "parent: x", `started_at: "2026-10-18T00:00:00Z"`)}},
	{"s-other", map[string]string{approvedDoing + "s-other.md": front("ticket: s-other-2")}},
	{"s-quoted", map[string]string{approvedDoing + "s-quoted.md": front(`ticket: "s-quoted"`)}},
	{"s-body", map[string]string{approvedDoing + "s-body.md": "---\nticket: s-body\n---\nparent: これは本文\n"}},
	{"s-archived-child", map[string]string{"archive/s-archived-child.md": front("ticket: s-archived-child", "parent: x")}},
	{"s-nostart", map[string]string{approvedDoing + "s-nostart.md": front("ticket: s-nostart")}},
	{"s-emptystart", map[string]string{approvedDoing + "s-emptystart.md": front("ticket: s-emptystart", `started_at: ""`)}},
	{"s-started", map[string]string{approvedDoing + "s-started.md": front("ticket: s-started", "started_at: 2026-10-18T00:00:00Z")}},
	{"s-todostart", map[string]string{"wip/proposals/todo/s-todostart.md": front("ticket: s-todostart", "started_at: 2026-10-18T00:00:00Z")}},
	{"s-marks", map[string]string{
		".ccnavi/approved/phases/s-marks/1.reviewed": "{}\n", ".ccnavi/approved/phases/s-marks/x.requested": "{}\n",
		".ccnavi/approved/phases/s-other/1.requested": "{}\n"}},
	{"s-marked", map[string]string{".ccnavi/approved/phases/s-marked/2.requested": "{}\n"}},
	{"s-mr", map[string]string{approvedDoing + "s-mr.md": front("ticket: s-mr", "mr: 430")}},
	{"s-mr0", map[string]string{approvedDoing + "s-mr0.md": front("ticket: s-mr0", "mr: 0")}},
	{"s-mr-neg", map[string]string{approvedDoing + "s-mr-neg.md": front("ticket: s-mr-neg", "mr: -3")}},
	{"s-mr-abc", map[string]string{approvedDoing + "s-mr-abc.md": front("ticket: s-mr-abc", "mr: abc")}},
	{"s-mr-list", map[string]string{approvedDoing + "s-mr-list.md": front("ticket: s-mr-list", "mr: [1]")}},
	{"s-mr-empty", map[string]string{approvedDoing + "s-mr-empty.md": front("ticket: s-mr-empty", `mr: ""`)}},
	{"s-mr-todo", map[string]string{"wip/proposals/todo/s-mr-todo.md": front("ticket: s-mr-todo", "mr: 5")}},
}

// putShapes は shapeCases を置く（ワークツリーは git を起こさずディレクトリだけ）。
func putShapes(t *testing.T, root string) {
	t.Helper()
	for _, c := range shapeCases {
		dir := testkit.Worktree(root, c.name)
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		for rel, text := range c.files {
			if name, ok := strings.CutPrefix(rel, "archive/"); ok {
				testkit.Put(t, filepath.Join(root, "logs", "archive", "self", "done", name), text)
				continue
			}
			testkit.Put(t, filepath.Join(dir, filepath.FromSlash(rel)), text)
		}
	}
}

func TestAnswersMatchGolden(t *testing.T) {
	root := shaskWorkspace(t)
	putShapes(t, root)
	w := func(name string) string { return testkit.Worktree(root, name) }
	nope := filepath.Join(root, "nope")
	cmds := [][]string{
		{"tree", "parent", w("i0001"), "i0001"},
		{"tree", "parent", w("i0002"), "i0002"},
		{"tree", "parent", w("i0003"), "i0003"},
		{"tree", "parent", w("i0003"), "I0003"},
		{"tree", "parent", w("i0004"), "i0004"},
		{"tree", "parent", w("fix-a"), "fix-a"},
		{"tree", "parent", root, "i0003"},
		{"tree", "parent", w("i0001"), "../x"},
		{"tree", "parent", nope, "i0001"},
		{"tree", "parent", w("i0001")},
		{"tree", "other", root, "x"},
	}
	for _, c := range shapeCases {
		cmds = append(cmds, []string{"tree", "parent", w(c.name), c.name})
	}
	want := golden.Load[[][3]any](t, "answers")
	conf := testkit.Settings(t, root)
	for i, cmd := range cmds {
		var out, errOut strings.Builder
		code, ok := Answer(&out, &errOut, root, conf, cmd)
		if !ok {
			code = -1
		}
		got := [3]any{float64(code), testkit.Rel(root, out.String()), testkit.Rel(root, errOut.String())}
		if i >= len(want) {
			t.Errorf("%q: 見本に行が無い。答え %q", cmd, got)
		} else if got != want[i] {
			t.Errorf("%q:\n got %q\nwant %q", cmd, got, want[i])
		}
	}
}

// 親のワークツリーに残っているもの（ccnavi-worktree.sh が切り替えの前後に見る）。
func TestLeftover(t *testing.T) {
	root := shaskWorkspace(t)
	w := func(name string) string { return testkit.Worktree(root, name) }
	conf := testkit.Settings(t, root)
	for _, c := range []struct {
		path, name string
		want       LeftoverFacts
	}{
		{w("i0001"), "i0001", LeftoverFacts{Leftover: true, Proposal: true, Doing: 2, MR: "7", Stage: "doing"}},
		{w("i0001"), "I0001", LeftoverFacts{}},
		{w("i0002"), "i0002", LeftoverFacts{}},
		{w("i0004"), "i0004", LeftoverFacts{Leftover: true, Proposal: true, MR: "１２", Stage: "review"}},
		{w("fix-a"), "fix-a", LeftoverFacts{}},
		{w("fix-a"), "a b", LeftoverFacts{}},
		{root, filepath.Base(root), LeftoverFacts{}},
	} {
		if got := Leftover(root, conf, c.path, c.name); got != c.want {
			t.Errorf("%s %s: %+v, want %+v", c.path, c.name, got, c.want)
		}
	}
}

// 手元の退避の親の MR は、大文字小文字だけが違う綴りの退避からも引く（回帰: 区別しないファイルシステムと区別するものと
// で答えが割れた）。
func TestArchivedMRIgnoresCase(t *testing.T) {
	root := testkit.TempRoot(t)
	testkit.Put(t, filepath.Join(root, "logs", "archive", "self", "done", "I0042.md"), "---\nversion: 1\nticket: I0042\nmr: 42\n---\n")
	if got := archivedMR(root, "", "i0042"); got != "42" {
		t.Errorf("i0042: %q", got)
	}
	if got := archivedMR(root, "", "i0043"); got != "" {
		t.Errorf("i0043: %q", got)
	}
}
