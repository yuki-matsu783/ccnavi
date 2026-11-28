package approval

import (
	"path/filepath"
	"testing"

	"ccnavi/internal/tickets/testkit"
)

// ChildParent は設定の置き場を読み、ワークスペース・ワークツリーのどこに置かれた子でも親を返す。
func TestChildParent(t *testing.T) {
	root := testkit.TempRoot(t)
	conf := testkit.Settings(t, root)
	worktree := filepath.Join(root, ".claude", "worktrees", "i0001-01-01")
	child := "---\nticket: i0001-01-01\nparent: i0001\n---\n"

	if _, ok := ChildParent(conf, root, "i0001-01-01"); ok {
		t.Fatal("チケットが無いのに子と判定された")
	}
	testkit.Put(t, filepath.Join(conf.ApprovedDir(worktree), "doing", "i0001-01-01.md"), child)
	if parent, ok := ChildParent(conf, root, "i0001-01-01"); !ok || parent != "i0001" {
		t.Errorf("親のツリーに置いた子 = %q, %v", parent, ok)
	}

	// 設定で置き場を変えても追える（決め打ちの `.ccnavi/approved` ではない）。
	moved := *conf
	moved.Approved = "state/approved"
	if _, ok := ChildParent(&moved, root, "i0001-01-01"); ok {
		t.Error("置き場を変えたのに、元の置き場を読んだ")
	}
	testkit.Put(t, filepath.Join(root, "state", "approved", "done", "i0002-01-01.md"), "---\nticket: i0002-01-01\nparent: i0002\n---\n")
	if parent, ok := ChildParent(&moved, root, "i0002-01-01"); !ok || parent != "i0002" {
		t.Errorf("変えた置き場の子 = %q, %v", parent, ok)
	}
}

// 読めないチケットと、親の欄が空のチケットは、子かもしれないとして止める側（親は UnknownParent）。
func TestChildParentUnreadIsChild(t *testing.T) {
	root := testkit.TempRoot(t)
	conf := testkit.Settings(t, root)
	for name, text := range map[string]string{
		"a": "壊れた本文\n",
		"b": "---\nticket: b\nparent:\n---\n",
		"c": "---\nticket: c\ntitle: 親\n---\n",
	} {
		testkit.Put(t, filepath.Join(root, "wip", "proposals", "review", name+".md"), text)
		parent, ok := ChildParent(conf, root, name)
		if want := name != "c"; ok != want {
			t.Errorf("%s: 子 = %v, want %v", name, ok, want)
		}
		if ok && parent != UnknownParent {
			t.Errorf("%s: 親 = %q", name, parent)
		}
	}
}
