package git

import (
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/shkit"
)

// ユーザが統合先をチェックアウトしているとき、merge と pull で別のブランチを取り込む形を事前に止める。
// ワークツリーの中の merge・pull は通す。形ごとの補い方の表は Go の TestMergeAndPullOnTheTarget が見る。ここは通しの確認。

// makeBranch は統合先から切った枝に 1 コミットを積んで、ワークツリーを外す。
func (f *itg) makeBranch(name string) {
	f.t.Helper()
	git(f.t, f.ws, "branch", name)
	side := filepath.Join(f.tmp, "side-"+name)
	git(f.t, f.ws, "worktree", "add", "-q", side, name)
	shkit.Write(f.t, filepath.Join(side, name+".txt"), name+"\n")
	git(f.t, side, "add", ".")
	git(f.t, side, "commit", "-q", "-m", name)
	git(f.t, f.ws, "worktree", "remove", "--force", side)
}

func TestMergeOnTargetEveryFormOfMergeOnTheTargetIsRefused(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.makeBranch("fa")
	for _, args := range [][]string{{"fa"}, {"--squash", "fa"}} {
		before := f.head()
		r := f.code(f.sh(append([]string{"merge"}, args...)...), 2)
		contains(t, r.Stderr, "統合先 main のユーザのチェックアウトでは merge できません", "次: ", "ccnavi-worktree.sh", "integrate <名前>")
		f.eq(f.head(), before, "HEAD")
		f.mustNotExist(f.p("fa.txt"), "fa.txt")
	}
}

func (f *itg) parentsOfHead(wt string) int {
	return len(strings.Fields(git(f.t, wt, "rev-list", "--parents", "-n", "1", "HEAD"))) - 1
}

// ワークツリーの中の merge は通す。自分のコミットが無くても印が残るよう --no-ff を補う。
func TestMergeOnTargetInAWorktreeWithoutOwnCommitsMakesAMergeCommit(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	upToDate := f.cut("uptodate", "", "main")
	before := gitT(t, upToDate, "rev-parse", "HEAD")
	f.code(f.shIn(upToDate, nil, "merge", "main", "--no-edit"))
	f.eq(gitT(t, upToDate, "rev-parse", "HEAD"), before, "取り込む物が無いときの HEAD")
	ffonly := f.cut("ffonly", "", "main")
	f.advanceMain("other.txt", "y\n")
	mainHead := f.head()

	f.code(f.shIn(f.wt, nil, "merge", "main", "--no-edit"))
	f.eq(f.parentsOfHead(f.wt), 2, "親の数")
	f.eq(gitT(t, f.wt, "rev-parse", "HEAD^2"), mainHead, "HEAD^2")
	f.eq(shkit.Read(t, filepath.Join(f.wt, "other.txt")), "y\n", "other.txt")

	f.code(f.shIn(ffonly, nil, "merge", "--ff-only", "main"))
	f.eq(gitT(t, ffonly, "rev-parse", "HEAD"), mainHead, "早送りの HEAD")
	f.eq(f.parentsOfHead(ffonly), 1, "早送りの親の数")
}

func TestMergeOnTargetAbortEndsAConflictedMergeOnTheTarget(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	shkit.Write(t, f.p("keep.txt"), "main\n")
	git(t, f.ws, "commit", "-q", "-am", "main side")
	git(t, f.ws, "branch", "fb", "HEAD~1")
	side := filepath.Join(f.tmp, "side-fb")
	git(t, f.ws, "worktree", "add", "-q", side, "fb")
	shkit.Write(t, filepath.Join(side, "keep.txt"), "fb\n")
	git(t, side, "commit", "-q", "-am", "fb side")
	gitTry(f.ws, "merge", "fb") // ユーザが直接打つ git
	f.mustExist(f.p(".git/MERGE_HEAD"), "MERGE_HEAD")
	f.code(f.sh("merge", "--abort"))
	f.mustNotExist(f.p(".git/MERGE_HEAD"), "MERGE_HEAD")
}

// ---- pull

// newPullFix は統合先のユーザのチェックアウトの pull を試す共通の準備（origin と、先へ進んだ main）。
func newPullFix(t *testing.T) *itg {
	t.Helper()
	f := newItg(t, true)
	origin := filepath.Join(f.tmp, "origin.git")
	git(t, f.tmp, "init", "-q", "--bare", "-b", "main", origin)
	git(t, f.ws, "remote", "add", "origin", origin)
	git(t, f.ws, "push", "-q", "origin", "main")
	git(t, f.ws, "fetch", "-q", "origin")
	git(t, f.ws, "branch", "--set-upstream-to=origin/main", "main")
	// origin に feat（main と分岐）と、先へ進んだ main を置く。
	side := filepath.Join(f.tmp, "side-origin")
	git(t, f.ws, "worktree", "add", "-q", side, "-b", "feat", "main")
	shkit.Write(t, filepath.Join(side, "feat.txt"), "feat\n")
	git(t, side, "add", ".")
	git(t, side, "commit", "-q", "-m", "feat")
	git(t, side, "push", "-q", "origin", "feat")
	git(t, side, "checkout", "-q", "--detach", "main")
	shkit.Write(t, filepath.Join(side, "next.txt"), "next\n")
	git(t, side, "add", ".")
	git(t, side, "commit", "-q", "-m", "next")
	git(t, side, "push", "-q", "origin", "HEAD:main")
	git(t, f.ws, "worktree", "remove", "--force", side)
	git(t, f.ws, "branch", "-D", "feat")
	return f
}

// 統合先のユーザのチェックアウトでは、pull でも別のブランチを merge できない。
func TestPullOnTargetPullingAnotherBranchOnTheTargetIsRefused(t *testing.T) {
	t.Parallel()
	f := newPullFix(t)
	for _, args := range [][]string{{"origin", "feat"}, {"--ff-only", "origin", "main", "next"}} {
		before := f.head()
		r := f.code(f.sh(append([]string{"pull"}, args...)...), 2)
		contains(t, r.Stderr, "統合先 main のユーザのチェックアウトでは", "別のブランチ・リモートを取り込めません", "次: ", "ccnavi-worktree.sh", "integrate <名前>")
		f.eq(f.head(), before, "HEAD")
		f.mustNotExist(f.p("feat.txt"), "feat.txt")
	}
}

func TestPullOnTargetUpdatingTheTargetItselfPasses(t *testing.T) {
	t.Parallel()
	f := newPullFix(t)
	for _, args := range [][]string{{"--ff-only"}, {"--ff-only", "origin", "main"}} {
		git(t, f.ws, "reset", "-q", "--hard", "origin/main~1")
		f.code(f.sh(append([]string{"pull"}, args...)...))
		f.mustExist(f.p("next.txt"), "next.txt")
	}
}

func TestPullOnTargetPullingAParentBranchInsideAWorktreePasses(t *testing.T) {
	t.Parallel()
	f := newPullFix(t)
	f.code(f.shIn(f.wt, nil, "pull", "--no-edit", "origin", "main"))
	f.eq(shkit.Read(t, filepath.Join(f.wt, "next.txt")), "next\n", "next.txt")
}

// 統合先のユーザのチェックアウトの pull が衝突したときだけ、ユーザへ頼む案内を足す。
const pullNote = "ユーザに pull とコンフリクト解消（または中止）を頼んで止まってください"

func newPullConflictFix(t *testing.T) *itg {
	t.Helper()
	f := newPullFix(t)
	git(t, f.ws, "config", "pull.rebase", "false")
	// origin の main が keep.txt の 2 行目を直す。
	side := filepath.Join(f.tmp, "side-conflict")
	git(t, f.ws, "worktree", "add", "-q", side, "-b", "side-c", "origin/main")
	shkit.Write(t, filepath.Join(side, "keep.txt"), "1\nremote\n3\n")
	git(t, side, "commit", "-q", "-am", "remote edit")
	git(t, side, "push", "-q", "origin", "HEAD:main")
	git(t, f.ws, "worktree", "remove", "--force", side)
	git(t, f.ws, "branch", "-D", "side-c")
	return f
}

func (f *itg) commitLocalEdit(cwd string) {
	f.t.Helper()
	shkit.Write(f.t, filepath.Join(cwd, "keep.txt"), "1\nlocal\n3\n")
	git(f.t, cwd, "commit", "-q", "-am", "local edit")
}

func TestPullConflictNoteConflictOnTheTargetAddsTheNote(t *testing.T) {
	t.Parallel()
	f := newPullConflictFix(t)
	f.commitLocalEdit(f.ws)
	r := f.code(f.sh("pull", "--no-edit"), 1)
	contains(t, r.Stdout, "CONFLICT", pullNote, "restore --theirs / --ours")
	// merge の途中なら、merge --abort は統合先のユーザのチェックアウトで通る。
	f.code(f.sh("merge", "--abort"))
	f.eq(shkit.Read(t, f.p("keep.txt")), "1\nlocal\n3\n", "keep.txt")
}

func TestPullConflictNoteConflictInsideAWorktreeHasNoNote(t *testing.T) {
	t.Parallel()
	f := newPullConflictFix(t)
	f.commitLocalEdit(f.wt)
	r := f.code(f.shIn(f.wt, nil, "pull", "--no-edit", "origin", "main"), 1)
	contains(t, r.Stdout, "CONFLICT")
	notContains(t, r.Stdout, pullNote)
	f.code(f.shIn(f.wt, nil, "merge", "--abort"))
	// 統合先でも、衝突しない pull には足さない。
	shkit.Write(t, f.p("mine.txt"), "m\n")
	git(t, f.ws, "add", "mine.txt")
	git(t, f.ws, "commit", "-q", "-m", "mine")
	r = f.code(f.sh("pull", "--no-edit"))
	notContains(t, r.Stdout, pullNote)
}
