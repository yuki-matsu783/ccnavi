package worktrees

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"ccnavi/internal/tickets/testkit"
)

// HEAD が detached のワークツリーで作ったコミットは、ワークツリーの HEAD しか辿らない。ほかの ref に無い
// コミットがあるうちは消さず、ブランチを付けたら消せる。
func TestDropKeepsDetachedCommits(t *testing.T) {
	root := testkit.ShapedWorkspace(t)
	testkit.Git(t, root, "worktree", "add", "-q", "--detach", filepath.Join(".claude", "worktrees", "d"), "main")
	tree := testkit.Worktree(root, "d")
	testkit.Put(t, filepath.Join(tree, "a.md"), "作業\n")
	testkit.Commit(t, tree, "work")

	d := Drop(root, "d", root)
	if d.Status != Refused || !strings.Contains(d.Detail, "detached") {
		t.Fatalf("消さずに止めるはず: %+v", d)
	}
	if b := Blocked(root, "d", root); b == nil || b.Status != Refused {
		t.Errorf("Blocked も同じ判定で止めるはず: %+v", b)
	}
	if _, err := os.Stat(tree); err != nil {
		t.Fatalf("ワークツリーが残っていない: %v", err)
	}

	testkit.Git(t, tree, "branch", "keep")
	if d := Drop(root, "d", root); d.Status != Removed {
		t.Fatalf("ブランチを付けたら消せるはず: %+v", d)
	}
}

// detached でも、HEAD がほかの ref から辿れる（新しいコミットが無い）なら消す。
func TestDropDetachedWithoutNewCommits(t *testing.T) {
	root := testkit.ShapedWorkspace(t)
	testkit.Git(t, root, "worktree", "add", "-q", "--detach", filepath.Join(".claude", "worktrees", "d"), "main")
	if d := Drop(root, "d", root); d.Status != Removed {
		t.Fatalf("消せるはず: %+v", d)
	}
}

// 隣の印（package.json）がリンクのとき、out/ は生成物と見ない。消さずに、無視されたユーザのファイルとして
// 退避する（生成物と見る判定と、消す判定が食い違うと、どちらもされずに worktree remove で消える）。
func TestDropStashesBesideLinkedMark(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("リンクを作れるとは限らない")
	}
	root := testkit.ShapedWorkspace(t)
	tree := testkit.AddWorktree(t, root, "w", "main")
	testkit.Put(t, filepath.Join(tree, ".gitignore"), "out/\n")
	testkit.Put(t, filepath.Join(tree, "real.json"), "{}")
	if err := os.MkdirAll(filepath.Join(tree, "ext"), 0o777); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join("..", "real.json"), filepath.Join(tree, "ext", "package.json")); err != nil {
		t.Skip("リンクを作れない: ", err)
	}
	testkit.Commit(t, tree, "files")
	testkit.Put(t, filepath.Join(tree, "ext", "out", "data.md"), "記録\n")

	if d := Drop(root, "w", root); d.Status != Removed {
		t.Fatalf("消せるはず: %+v", d)
	}
	stashed := filepath.Join(root, "scratchpad", "worktrees", "w", "ext", "out", "data.md")
	if _, err := os.Stat(stashed); err != nil {
		t.Fatalf("退避されていない: %v", err)
	}
}
