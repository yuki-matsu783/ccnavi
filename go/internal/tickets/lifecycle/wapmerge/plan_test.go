package wapmerge

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/testkit"
)

// 合流の前の検査（子のワークツリーの未コミット・親で無視されるファイル）と、コミットと合流の細目。

const childID = "i0001-01-01"

// コミットは書いたパスだけ。索引にあるほかの変更は入れずに残し、ユーザの commit の hook は通さない。
func TestCommitLeavesOtherStagedAndSkipsHooks(t *testing.T) {
	root := localRepo(t)
	hooks := filepath.Join(root, ".git", "hooks")
	for _, name := range []string{"pre-commit", "commit-msg"} {
		testkit.Put(t, filepath.Join(hooks, name), "#!/bin/sh\nexit 1\n")
		if err := os.Chmod(filepath.Join(hooks, name), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	testkit.Put(t, filepath.Join(root, "src", "other.py"), "x = 1\n")
	testkit.Git(t, root, "add", "--", "src/other.py")
	testkit.Put(t, filepath.Join(root, "s", "new.json"), "new\n")
	got := Commit(root, []string{"s/new.json"}, "ccnavi: x", time.Minute)
	if !got.OK || got.Sha == "" {
		t.Fatalf("コミットできない（hook を通した？）: %+v", got)
	}
	if paths := strings.Fields(testkit.Git(t, root, "show", "--name-only", "--format=", "HEAD")); !slices.Equal(paths, []string{"s/new.json"}) {
		t.Fatalf("コミットしたパス %v", paths)
	}
	if staged := strings.Fields(testkit.Git(t, root, "diff", "--cached", "--name-only")); !slices.Equal(staged, []string{"src/other.py"}) {
		t.Fatalf("索引に残ったもの %v", staged)
	}
}

// 衝突したパスは、空白や日本語を含んでもそのまま並べ、解くのは後から finish した子と名指す。
func TestBeginConflictNamesSpacedAndJapanesePaths(t *testing.T) {
	root := localRepo(t)
	rels := []string{"s/a b.md", "s/日本.md"}
	put := func(text string) {
		for _, rel := range rels {
			testkit.Put(t, filepath.Join(root, filepath.FromSlash(rel)), text)
		}
	}
	testkit.Git(t, root, "checkout", "-q", "-b", "child")
	put("child\n")
	testkit.Commit(t, root, "child")
	child := Head(root)
	testkit.Git(t, root, "checkout", "-q", "main")
	put("parent\n")
	testkit.Commit(t, root, "parent")
	_, said, ok := Begin(root, Head(root), child, childID, "main")
	if ok || len(said) != 2 {
		t.Fatalf("衝突で止まらない: %v", said)
	}
	for _, s := range []string{"衝突する（s/a b.md, s/日本.md）", "解くのは後から finish した側（この子）"} {
		if !strings.Contains(said[0], s) {
			t.Errorf("%q が無い: %s", s, said[0])
		}
	}
	if !strings.Contains(said[1], "子のワークツリー（.claude/worktrees/"+childID+"）") {
		t.Errorf("子のワークツリーを名指さない: %s", said[1])
	}
}

// 子のワークツリーに未コミットの変更（未追跡を含む）があれば、合流の前に止める。
func TestTreeProblemChildUncommitted(t *testing.T) {
	root := testkit.ShapedWorkspace(t)
	child := testkit.AddWorktree(t, root, childID, "main")
	if why, _, bad := treeProblem(&config.Settings{}, root, child, childID, false); bad {
		t.Fatalf("きれいな子で止めた: %s", why)
	}
	testkit.Put(t, filepath.Join(child, "wip", "design", "late.md"), "x\n")
	why, hints, bad := treeProblem(&config.Settings{}, root, child, childID, false)
	if !bad || !strings.Contains(why, "未コミットの変更がある（wip/design/late.md）") ||
		!strings.Contains(why, "サブエージェントの作業がコミットされていません") || len(hints) != 1 {
		t.Fatalf("止まらない: %v %q %v", bad, why, hints)
	}
}

// 子が変えたパスが親のワークツリーに無視されたファイルとしてあれば挙げる（合流すると黙って上書きする）。
func TestIgnoredInParent(t *testing.T) {
	root := testkit.ShapedWorkspace(t)
	base := testkit.Head(t, root)
	child := testkit.AddWorktree(t, root, childID, "main")
	testkit.Put(t, filepath.Join(root, ".git", "info", "exclude"), "wip/design/secret.md\n")
	testkit.Put(t, filepath.Join(root, "wip", "design", "secret.md"), "USER SECRET\n")
	for _, rel := range []string{"wip/design/secret.md", "wip/design/a.md"} {
		testkit.Put(t, filepath.Join(child, filepath.FromSlash(rel)), "child\n")
	}
	testkit.Git(t, child, "add", "-f", "--", "wip/design")
	testkit.Git(t, child, "commit", "-q", "-m", "work")
	found, why := ignoredInParent(root, child, base, testkit.Head(t, child))
	if why != "" || !slices.Equal(found, []string{"wip/design/secret.md"}) {
		t.Fatalf("got %v %q", found, why)
	}
}
