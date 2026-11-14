package shask

import (
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

func TestAnswersMatchGolden(t *testing.T) {
	root := shaskWorkspace(t)
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
		{"tree", "bound", w("fix-a"), "fix-a"},
		{"tree", "bound", w("i0001"), "i0001"},
		{"tree", "bound", nope, "fix-a"},
		{"tree", "leftover", w("i0001"), "i0001"},
		{"tree", "leftover", w("i0001"), "I0001"},
		{"tree", "leftover", w("i0002"), "i0002"},
		{"tree", "leftover", w("i0004"), "i0004"},
		{"tree", "leftover", w("fix-a"), "fix-a"},
		{"tree", "leftover", w("fix-a"), "a b"},
		{"tree", "base-bound", root, "main"},
		{"tree", "base-bound", root, "i0001"},
		{"tree", "base-bound", root, "a\nb"},
		{"tree", "base-bound", root, ""},
		{"tree", "base-bound", nope, "main"},
		{"tree", "other", root, "x"},
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
		if i >= len(want) || got != want[i] {
			t.Errorf("%q:\n got %q\nwant %q", cmd, got, want[i])
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
