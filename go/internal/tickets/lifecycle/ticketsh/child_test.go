package ticketsh

import (
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/tickets/testkit"
)

// 子のワークツリーが無ければ親の先頭から作る。手元に残った子のブランチは、親の先頭が含むなら揃えて使い、
// 含まない（手元だけのコミットがある）なら消さずに止める。
func TestChildReusesABranchBehindAndKeepsOneAhead(t *testing.T) {
	const child = "i0001-01-01"
	for _, c := range []struct {
		name  string
		setup func(t *testing.T, root, parent string)
		want  string
	}{
		{"無ければ作る", func(t *testing.T, root, parent string) {}, "made i0001"},
		{"親の先頭が含む子のブランチは揃えて使う", func(t *testing.T, root, parent string) {
			testkit.Git(t, root, "branch", child, "main")
		}, "made i0001"},
		{"手元だけのコミットがある子のブランチは止める", func(t *testing.T, root, parent string) {
			side := testkit.AddWorktree(t, root, child, "main")
			testkit.Put(t, filepath.Join(side, "mine.txt"), "x\n")
			testkit.Commit(t, side, "mine")
			testkit.Git(t, root, "worktree", "remove", "--force", side)
		}, "child-branch-ahead"},
	} {
		t.Run(c.name, func(t *testing.T) {
			root := testkit.Workspace(t, "i0001")
			parent := testkit.Worktree(root, "i0001")
			testkit.Commit(t, parent, "parent work")
			c.setup(t, root, parent)
			got := Child(root, child, parent, "i0001", "i0001")
			if !strings.Contains(got, c.want) {
				t.Fatalf("答え %q に %q が無い", got, c.want)
			}
			dest := testkit.Worktree(root, child)
			if c.want == "child-branch-ahead" {
				if !strings.Contains(got, "に無いコミットがある") {
					t.Errorf("理由が無い: %q", got)
				}
				return
			}
			if testkit.Head(t, dest) != testkit.Head(t, parent) {
				t.Error("親の先頭から切れていない")
			}
			if b := strings.TrimSpace(testkit.Git(t, dest, "rev-parse", "--abbrev-ref", "HEAD")); b != child {
				t.Errorf("ブランチ %s", b)
			}
		})
	}
}
