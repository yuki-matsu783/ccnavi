package life

import (
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/ticketkit"
)

// 直接作業が integrate まで進んでいないときの Stop の促し。促すのは、チケットに結び付かない `.claude/worktrees/<名前>` で、
// 統合先に入っていないコミットがあり、`integrate <名前>` の記録が無いとき。同じ状態では 1 回だけ。未コミットの変更だけでは促さない。
// 起点 S からの数え方・途中の操作・別のリポジトリの記録・origin の有無・integrate が断る名前は、本体の
// tickets/lifecycle/integrate の at_stop_test.go が場面ごとに見る。ここは hook を通した経路だけ。

func integrateTree(t *testing.T) *ticketkit.Tree {
	t.Helper()
	tr := ticketkit.New(t)
	// ccnavi 自身の置き場（設定・記録・state）は、本番ではリポジトリが無視している。統合先のツリーを汚すものは、テストが置く src/ の
	// 下のファイルだけにする。
	exclude := strings.TrimSpace(tr.Git(tr.Root, "rev-parse", "--git-path", "info/exclude"))
	if !filepath.IsAbs(exclude) {
		exclude = filepath.Join(tr.Root, exclude)
	}
	ticketkit.Write(t, exclude, "/logs/\n/.ccnavi/config/\n")
	return tr
}

// integrateStop は Stop を起こす。統合先は手元の origin/HEAD で決まるので、origin が無ければ足して main に向ける。
func integrateStop(tr *ticketkit.Tree, cwd string, opts ...stopOpts) run.Result {
	tr.T.Helper()
	remotes := strings.Fields(tr.Git(tr.Root, "remote"))
	if !contains(remotes, "origin") {
		tr.Git(tr.Root, "remote", "add", "origin", tr.Root+".origin.git")
	}
	tr.Git(tr.Root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
	return stop(tr, cwd, opts...)
}

func contains(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}

// direct はチケットに結び付かない直接作業のワークツリー。
func direct(tr *ticketkit.Tree, commit bool) string {
	tr.T.Helper()
	path := tr.Worktree("scratch", "main")
	if commit {
		commitWork(tr, path, "src/a/work.py")
	}
	return path
}

// putDirectRecord は ccnavi-worktree.sh が置く直接作業の記録（version 4）。起点 S は main の先頭。
func putDirectRecord(tr *ticketkit.Tree) {
	tr.T.Helper()
	common := strings.TrimSpace(tr.Git(tr.Root, "rev-parse", "--git-common-dir"))
	if !filepath.IsAbs(common) {
		common = filepath.Join(tr.Root, common)
	}
	realCommon, err := filepath.EvalSymlinks(common)
	if err != nil {
		tr.T.Fatal(err)
	}
	mainTree, err := filepath.EvalSymlinks(tr.Root)
	if err != nil {
		tr.T.Fatal(err)
	}
	snap := strings.TrimSpace(tr.Git(tr.Root, "rev-parse", "main"))
	fields := [][2]string{{"version", "4"}, {"base_branch", "main"}, {"base_sha", snap}, {"snap_sha", snap}, {"snap_wip", "no"},
		{"side_by_side", "no"}, {"main_tree", mainTree}, {"common_dir", realCommon}, {"confirm", "-"}, {"created", "1"}}
	var text strings.Builder
	for _, f := range fields {
		text.WriteString(f[0] + " " + f[1] + "\n")
	}
	ticketkit.Write(tr.T, filepath.Join(realCommon, "ccnavi-direct", "scratch"), text.String())
}

func askedIntegrate(t *testing.T, r run.Result) string {
	t.Helper()
	reason := asked(t, r, codeIntegrate)
	ticketkit.Has(t, reason, "[ccnavi] "+codeIntegrate+" (worktree: scratch)")
	return reason
}

func TestCommitsNotInTheBaseBranchAreAskedOnce(t *testing.T) {
	t.Parallel()
	tr := integrateTree(t)
	path := direct(tr, true)
	putDirectRecord(tr)
	reason := askedIntegrate(t, integrateStop(tr, path))
	cd := "cd " + base.ShellPath(tr.Root) + " && " + base.ScriptCommand(tr.Root, "ccnavi-git.sh")
	for _, w := range []string{"`" + cd + " integrate scratch`", "`" + cd + " integrate-done scratch -m '<メッセージ>'`", "`" + cd + " integrate --abort scratch`",
		"一時ワークツリー", "取り込み先（main）に入っていないコミット 1 件", filepath.Base(tr.Root)} {
		ticketkit.Has(t, reason, w)
	}
	ticketkit.Lacks(t, reason, "統合先")
	// 同じ状態では 1 回だけ。連鎖の 2 回目も止めない。
	quiet(t, integrateStop(tr, path), codeIntegrate)
	quiet(t, integrateStop(tr, path, stopOpts{Active: true}), codeIntegrate)
	// 進めば（コミットを足せば）また 1 回。
	commitWork(tr, path, "src/more.py")
	ticketkit.Has(t, askedIntegrate(t, integrateStop(tr, path)), "コミット 2 件")
}

// 判定の材料は記録だけ。hook は integrate を打たない（統合先のツリーは変わらない）。
func TestTheIntegrateNudgeDoesNothingButTalk(t *testing.T) {
	t.Parallel()
	tr := integrateTree(t)
	path := direct(tr, true)
	before := tr.Git(tr.Root, "status", "--porcelain")
	askedIntegrate(t, integrateStop(tr, path))
	if got := tr.Git(tr.Root, "status", "--porcelain"); got != before {
		t.Errorf("統合先のツリーが変わった: %q → %q", before, got)
	}
	if got := strings.TrimSpace(tr.Git(tr.Root, "branch", "--list", "scratch")); got != "+ scratch" {
		t.Errorf("ブランチ = %q", got)
	}
}

func TestTheIntegrateNudgeIsNotGivenInAChainOrASubagentButTicketControlDoesNotMatter(t *testing.T) {
	t.Parallel()
	tr := integrateTree(t)
	path := direct(tr, true)
	quiet(t, integrateStop(tr, path, stopOpts{Active: true}), codeIntegrate)
	quiet(t, integrateStop(tr, path, stopOpts{Event: "SubagentStop", AgentID: "a1"}), codeIntegrate)
	// チケット制御が無効でも、直接作業はチケットに結び付かないので促す。
	askedIntegrate(t, integrateStop(tr, path, stopOpts{Session: "s9", Extra: []string{"--ticket-control", "disable"}}))
}

func TestAnIntegrateNudgeIsHeldByARunningSubagent(t *testing.T) {
	t.Parallel()
	tr := integrateTree(t)
	path := direct(tr, true)
	holdsWhileASubagentRuns(tr, path, func() run.Result { return integrateStop(tr, path) }, codeIntegrate)
}

// finish の促しで止める回は、直接作業の促しを出さず、数えもしない。
func TestTheFinishNudgeComesBeforeTheIntegrateNudge(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	tree := tr.ChildTree(childName)
	commitWork(tr, tree, "src/a/work.py")
	ex := strings.TrimSpace(tr.Git(tr.Root, "rev-parse", "--git-path", "info/exclude"))
	if !filepath.IsAbs(ex) {
		ex = filepath.Join(tr.Root, ex)
	}
	ticketkit.Write(t, ex, "/logs/\n/.ccnavi/config/\n")
	loose := direct(tr, true)
	ticketkit.Has(t, reasonOf(body(t, integrateStop(tr, tree))), codeFinish)
	askedIntegrate(t, integrateStop(tr, loose))
}
