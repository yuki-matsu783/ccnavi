package standing

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/testkit"
)

// workspace はワークスペースと、親のワークツリー（フォルダ名 → チェックアウトするブランチ）を作る。
func workspace(t *testing.T, trees map[string]string) string {
	t.Helper()
	root := testkit.GitRoot(t)
	testkit.Git(t, root, "init", "-q", "-b", "main")
	testkit.Git(t, root, "commit", "-q", "--allow-empty", "-m", "init")
	for name, branch := range trees {
		testkit.Git(t, root, "worktree", "add", "-q", "-b", branch, filepath.Join(".claude", "worktrees", name))
	}
	return root
}

func TestStanding(t *testing.T) {
	root := workspace(t, map[string]string{"feature-1-x": "feature/1-x", "feature-2-y": "other"})
	fams := NewFamilies(&config.Settings{}, root)

	home := fams.Standing("feature-1-x", "")
	if home.Stop != "" || home.Home == nil || home.Branch != "feature/1-x" || home.Repo != "self" {
		t.Errorf("親のワークツリーが正にならない: %+v", home)
	}
	// フォルダ名の大文字小文字が違っても同じワークツリーとして引く。
	if st := fams.Standing("FEATURE-1-X", ""); st.Kind != StopMismatch {
		t.Errorf("大文字のフォルダ名: %+v", st)
	}
	mismatch := fams.Standing("feature-2-y", "")
	if mismatch.Kind != StopMismatch || mismatch.Head != "other" || mismatch.Home != nil {
		t.Errorf("HEAD の食い違い: %+v", mismatch)
	}
	absent := fams.StandingAny("feature-3-z")
	if absent.Kind != StopAbsent || !strings.Contains(absent.Stop, ".claude/worktrees/feature-3-z") {
		t.Errorf("親のワークツリーが無い: %+v", absent)
	}
	if got := fams.StandingAny("feature-1-x"); got.Home == nil {
		t.Errorf("リポジトリの分からない引き方: %+v", got)
	}
	if fams.Branch("feature-2-y", "") != "" || fams.Branch("feature-1-x", "") != "feature/1-x" {
		t.Error("Branch")
	}
}

func TestBusyStops(t *testing.T) {
	root := workspace(t, map[string]string{"feature-4-x": "other"})
	gitDir := filepath.Join(root, ".git", "worktrees", "feature-4-x")
	if err := os.WriteFile(filepath.Join(gitDir, "MERGE_HEAD"), []byte("0\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if st := NewFamilies(&config.Settings{}, root).Standing("feature-4-x", ""); st.Kind != StopBusy {
		t.Errorf("途中の操作: %+v", st)
	}
}

// 文は種別ごとに文面ファイルから引き、見出し（次: ）を外して返す。
func TestGuidance(t *testing.T) {
	root := testkit.RepoRoot(t)
	for _, kind := range []StopKind{StopBusy, StopMismatch, StopAmbiguous, StopAbsent, StopNone} {
		lines := Guidance(root, Standing{Family: "feature-1-x", Kind: kind, Head: "h"})
		if len(lines) != 1 || lines[0] == "" || strings.HasPrefix(lines[0], "次: ") {
			t.Errorf("%s: %q", kind, lines)
		}
	}
	if Guidance(root, Standing{Family: "f", Kind: StopNone})[0] != Guidance(root, Standing{Family: "f", Kind: StopAbsent})[0] {
		t.Error("種別の無い立ち位置は absent の文にする")
	}
}

func TestHeadBranchKeepsTheBranchSpelling(t *testing.T) {
	root := workspace(t, map[string]string{"feature-5-x": "feature/5-x"})
	work := filepath.Join(root, ".claude", "worktrees", "feature-5-x")
	if HeadBranch(work, "feature-5-x") != "feature/5-x" || HeadBranch(work, "feature-5-y") != "" {
		t.Error("HeadBranch")
	}
}

func TestSameTree(t *testing.T) {
	if !SameTree("/a/b/", "/a/b") || SameTree("", "") || SameTree("/a", "/b") {
		t.Error("SameTree")
	}
}
