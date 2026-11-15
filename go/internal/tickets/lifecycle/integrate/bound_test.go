package integrate

import (
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/tickets/testkit"
)

// 直接作業のワークツリーの名前がチケットに結び付くか（ccnavi-worktree.sh --adopt が断る条件。Stop の促しと同じ判定）。
// files は置くファイル（ワークスペースからの相対 → 中身）、origin は origin/<名前> を置くか、inWhy は理由に含まれる語
// （空なら結び付かない）。
func TestBoundReasonScenarios(t *testing.T) {
	ticket := "---\nticket: p0001\n---\n本文\n"
	for _, c := range []struct {
		label  string
		files  []string
		text   string
		origin bool
		inWhy  string
	}{
		{"何も無い", nil, ticket, false, ""},
		{"承認済みの doing", []string{".ccnavi/approved/doing/p0001.md"}, ticket, false, ".ccnavi/approved/doing/p0001.md"},
		{"承認済みの done", []string{".ccnavi/approved/done/p0001.md"}, ticket, false, ".ccnavi/approved/done/p0001.md"},
		{"提案の todo", []string{"wip/proposals/todo/p0001.md"}, ticket, false, "wip/proposals/todo/p0001.md"},
		{"提案の review", []string{"wip/proposals/review/p0001.md"}, ticket, false, "wip/proposals/review/p0001.md"},
		{"外れたワークツリーの残骸", []string{".claude/worktrees/stale/.ccnavi/approved/doing/p0001.md"}, ticket, false, "stale"},
		{"git リポジトリでないプロジェクト", []string{"projects/pp/.ccnavi/approved/doing/p0001.md"}, ticket, false, "projects/pp"},
		{"ファイル名の大文字小文字", []string{".ccnavi/approved/doing/P0001.md"}, "---\nticket: p\n---\n", false, "P0001.md"},
		{"origin のブランチ", nil, ticket, true, "origin/p0001"},
	} {
		t.Run(c.label, func(t *testing.T) {
			root := testkit.GitRoot(t)
			testkit.Git(t, root, "init", "-q", "-b", "main")
			testkit.Commit(t, root, "init")
			tree := testkit.Worktree(root, "p0001")
			testkit.Git(t, root, "worktree", "add", "-q", "-b", "p0001", tree)
			if c.origin {
				testkit.Git(t, root, "update-ref", "refs/remotes/origin/p0001", "p0001")
			}
			for _, rel := range c.files {
				testkit.Put(t, filepath.Join(root, filepath.FromSlash(rel)), c.text)
			}
			conf := testkit.Settings(t, root)
			why := filepath.ToSlash(BoundReason(conf, root, TreeAt(conf, root, tree, "p0001")))
			if (why != "") != (c.inWhy != "") || !strings.Contains(why, c.inWhy) {
				t.Errorf("%q, want %q", why, c.inWhy)
			}
		})
	}
}
