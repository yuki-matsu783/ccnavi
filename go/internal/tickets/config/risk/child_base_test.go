package risk

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/git"
	"ccnavi/internal/tickets/testkit"
)

// リスクの計測は、統合先（リモート追跡ブランチ）から子へ取り込んだだけのパスを数えない
// （tests/core/test_child_base.py の Go 版）。数えると、取り込んだ大きな差分で子が重い側へ倒れる。
func TestMeasureLeavesOutPathsTakenFromTheIntegrationBranch(t *testing.T) {
	base := testkit.GitRoot(t)
	parent, child, up := filepath.Join(base, "parent"), filepath.Join(base, "child"), filepath.Join(base, "up")
	if err := os.Mkdir(parent, 0o755); err != nil {
		t.Fatal(err)
	}
	commit := func(dir, name, text string) {
		testkit.Put(t, filepath.Join(dir, name), text)
		runGit(t, dir, "add", "--", name)
		runGit(t, dir, "commit", "-q", "-m", name)
	}
	runGit(t, parent, "init", "-q", "-b", "p")
	commit(parent, "seed", "x\n")
	start := runGit(t, parent, "rev-parse", "HEAD")
	runGit(t, parent, "worktree", "add", "-q", "-b", "c", child, "p")
	// 統合先（origin/m）を着手のときの親の先頭から進める。親のブランチは動かさない。
	runGit(t, parent, "worktree", "add", "-q", "-b", "m", up, start)
	commit(up, "theirs", strings.Repeat("t\n", 50))
	runGit(t, parent, "update-ref", "refs/remotes/origin/m", runGit(t, up, "rev-parse", "HEAD"))

	commit(child, "mine", "x\n")
	runGit(t, child, "merge", "-q", "--no-edit", "origin/m")
	from, err := git.ChildBase(child, parent, 0, "")
	if err != nil {
		t.Fatal(err)
	}
	diff, why := Measure(child, from, "HEAD")
	if diff == nil {
		t.Fatal(why)
	}
	var paths []string
	for _, c := range diff.changes {
		paths = append(paths, c.path)
	}
	if len(paths) != 1 || paths[0] != "mine" {
		t.Errorf("数えたパス %v（取り込んだ theirs を数えない）", paths)
	}
}
