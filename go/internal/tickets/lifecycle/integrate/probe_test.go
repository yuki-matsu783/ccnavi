package integrate

import (
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/testkit"
)

// 記録のある直接作業を Stop で調べる判断（probeRecorded）の、golden に無い形。入っていないコミットの数え方（起点 S から数える）、
// 促せない理由（別のリポジトリの記録・取り込み先の切り替わり・ユーザのチェックアウトの途中の操作）、integrate が断る名前。
// 組み立ては golden_test.go の integrateWorkspace と同じ（直接作業のワークツリーと、記録は共通ディレクトリの ccnavi-direct）。

// recordedWorkspace は、main に origin を付け、記録のある直接作業 scratch（コミット 1 件）を持つワークスペースを組む。
func recordedWorkspace(t *testing.T, recordLines func(snap, gitDir string) []string) (root string, here tree.Tree) {
	t.Helper()
	root = clock.Workspace(t)
	remote := filepath.Join(t.TempDir(), "remote.git")
	clock.Git(t, root, "init", "-q", "--bare", remote)
	clock.Git(t, root, "remote", "add", "origin", remote)
	clock.Git(t, root, "push", "-q", "origin", "main")
	clock.Git(t, root, "remote", "set-head", "origin", "main")
	snap := strings.TrimSpace(clock.Git(t, root, "rev-parse", "HEAD"))
	gitDir := filepath.Join(root, ".git")
	testkit.AddWorktree(t, root, "scratch", "main")
	testkit.Put(t, filepath.Join(testkit.Worktree(root, "scratch"), "src", "a"), "a\n")
	commitAll(t, testkit.Worktree(root, "scratch"), "a")
	testkit.Put(t, filepath.Join(gitDir, "ccnavi-direct", "scratch"), strings.Join(recordLines(snap, gitDir), "\n")+"\n")
	conf := testkit.Settings(t, root)
	here, ok := tree.TreeOf(root, testkit.Worktree(root, "scratch"), conf.Projects)
	if !ok {
		t.Fatal("scratch")
	}
	return root, here
}

func standardRecord(snap, gitDir string) []string {
	return []string{"version 4", "base_branch main", "snap_sha " + snap, "common_dir " + gitDir}
}

func TestProbeRecordedDirectWork(t *testing.T) {
	look := func(t *testing.T, root string, here tree.Tree) Probe {
		return probe(testkit.Settings(t, root), root, here, time.Time{})
	}
	t.Run("commits are counted from the recorded start point", func(t *testing.T) {
		root, here := recordedWorkspace(t, func(_, gitDir string) []string { return nil })
		// 起点を先頭のコミットにする（S より前のコミットは数えない）
		start := strings.TrimSpace(clock.Git(t, here.Root, "rev-parse", "HEAD"))
		testkit.Put(t, filepath.Join(root, ".git", "ccnavi-direct", "scratch"), strings.Join(standardRecord(start, filepath.Join(root, ".git")), "\n")+"\n")
		if got := look(t, root, here); got.state != Clear {
			t.Errorf("S 以降のコミットが無いのに %v", got.state)
		}
		testkit.Put(t, filepath.Join(here.Root, "src", "more"), "m\n")
		commitAll(t, here.Root, "more")
		got := look(t, root, here)
		if got.state != hit || got.found.ahead != 1 || got.found.Blocked != "" {
			t.Errorf("S から 1 件のはず: %v %+v", got.state, got.found)
		}
	})
	t.Run("a record of another repository is reported without stopping", func(t *testing.T) {
		root, here := recordedWorkspace(t, func(snap, _ string) []string {
			return []string{"version 4", "base_branch main", "snap_sha " + snap, "common_dir " + filepath.Join(filepath.Dir(t.TempDir()), "elsewhere", ".git")}
		})
		got := look(t, root, here)
		if got.state != hit || !strings.Contains(got.found.Blocked, "共通ディレクトリ") {
			t.Errorf("%v %+v", got.state, got.found)
		}
	})
	for _, c := range []struct{ name, file, want string }{
		{"a merge in progress in the main checkout", "MERGE_HEAD", "MERGE_HEAD"},
		{"a squash in progress in the main checkout", "SQUASH_MSG", "SQUASH_MSG"},
	} {
		t.Run(c.name+" is reported without stopping", func(t *testing.T) {
			root, here := recordedWorkspace(t, func(snap, gitDir string) []string { return standardRecord(snap, gitDir) })
			testkit.Put(t, filepath.Join(root, ".git", c.file), strings.Repeat("0", 40)+"\n")
			got := look(t, root, here)
			if got.state != hit || !strings.Contains(got.found.Blocked, c.want) {
				t.Errorf("%v %+v", got.state, got.found)
			}
		})
	}
	t.Run("a switched base branch only tells the reason", func(t *testing.T) {
		root, here := recordedWorkspace(t, func(snap, gitDir string) []string { return standardRecord(snap, gitDir) })
		clock.Git(t, root, "switch", "--quiet", "-c", "other")
		got := look(t, root, here)
		if got.state != hit || !strings.Contains(got.found.Blocked, "取り込み先が切り替わっています") {
			t.Fatalf("%v %+v", got.state, got.found)
		}
		if notice := BlockedNotice(got.found); strings.Contains(notice, "取り込みの途中") {
			t.Errorf("途中でないのに途中と言っている: %s", notice)
		}
	})
	t.Run("a switched base branch during integrate says it is in progress", func(t *testing.T) {
		root, here := recordedWorkspace(t, func(snap, gitDir string) []string { return standardRecord(snap, gitDir) })
		testkit.Put(t, filepath.Join(root, ".git", RecordDir, "scratch", "marker"), "version 4\n")
		clock.Git(t, root, "switch", "--quiet", "-c", "other")
		got := look(t, root, here)
		if got.state != hit || got.found.Blocked == "" {
			t.Fatalf("%v %+v", got.state, got.found)
		}
		if notice := BlockedNotice(got.found); !strings.Contains(notice, "今は取り込みを促せません") {
			t.Errorf("文: %s", notice)
		}
	})
}

// integrate が断る名前（統合先に使う名前）の直接作業は、コミットがあっても促さず、候補から外す。
func TestProbeLeavesTheNamesIntegrateRefuses(t *testing.T) {
	root := clock.Workspace(t)
	remote := filepath.Join(t.TempDir(), "remote.git")
	clock.Git(t, root, "init", "-q", "--bare", remote)
	clock.Git(t, root, "remote", "add", "origin", remote)
	clock.Git(t, root, "push", "-q", "origin", "main")
	clock.Git(t, root, "remote", "set-head", "origin", "main")
	conf := testkit.Settings(t, root)
	for _, name := range []string{"release-x", "master", "develop", "release"} {
		testkit.AddWorktree(t, root, name, "main")
		testkit.Put(t, filepath.Join(testkit.Worktree(root, name), "src", "draft"), "y\n")
		commitAll(t, testkit.Worktree(root, name), "draft")
		here, ok := tree.TreeOf(root, testkit.Worktree(root, name), conf.Projects)
		if !ok {
			t.Fatal(name)
		}
		if got := probe(conf, root, here, time.Time{}); got.state != Clear || !got.drop || got.found != nil {
			t.Errorf("%s: %v drop=%v found=%v", name, got.state, got.drop, got.found)
		}
	}
}
