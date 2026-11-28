package life

import (
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// 正とするツリーの外に残った古い提案（承認済みの識別子）。承認済みの識別子の提案は、承認済みチケットと合わせて正とするツリー
// （親のツリー → 元ツリー → 決まらない）を決め、そのツリーに在るものだけを読む。
//  1. 改版の前に切ったワークツリーに残った古い版（場面 C）を、改版として承認の対象にしない
//  2. 正とするツリーで進めている改版（todo/ と doing/ が並ぶ）は、承認の対象に残る
//  3. 正としないツリーに書いた改版は承認の対象にならず、--lint が書く場所を案内する

const whereToWrite = "改版なら .claude/worktrees/i0001 の wip/proposals/todo/ に書き"

func parentWithPlan(tr *ticketkit.Tree, plan ...string) string {
	return samples.ParentText("i0001", samples.Plan(plan...), samples.ParentOpts{Copy: tr.Phases})
}

func pendingApproval(tr *ticketkit.Tree) []string {
	tr.T.Helper()
	var out struct {
		Pending []string `json:"pending_approval"`
	}
	jsonOf(tr.T, ticketkit.OK(tr.T, tr.Ccnavi("--explain", "--json")).Stdout, &out)
	return out.Pending
}

func lintLinesWith(tr *ticketkit.Tree, id string) []string {
	tr.T.Helper()
	var out []string
	for _, line := range strings.Split(tr.Ccnavi("--lint", "--mode", "enable").Stdout, "\n") {
		if strings.Contains(line, id) {
			out = append(out, line)
		}
	}
	return out
}

func anyLine(lines []string, parts ...string) bool {
	for _, line := range lines {
		ok := true
		for _, p := range parts {
			ok = ok && strings.Contains(line, p)
		}
		if ok {
			return true
		}
	}
	return false
}

// 場面 C。改版 1 が todo/ に在る間に切ったワークツリーに、改版 1 が残る。改版 1 → 改版 2 と承認したあと、残った改版 1 を改版と読むと、
// 承認待ちに入り、承認すると計画が巻き戻る。
func TestAnOldRevisionLeftInAWorktreeIsNotOfferedAgain(t *testing.T) {
	t.Parallel()
	tr := phaseFamily(t)
	tr.Propose("i0001", parentWithPlan(tr, "research", "design", "chores"), "")
	tr.CommitParent("tickets")
	tr.Worktree("i0001-01-07", "i0001")
	ticketkit.OK(t, tr.Approve())
	tr.Propose("i0001", parentWithPlan(tr, "research", "chores"), "")
	tr.CommitParent("tickets")
	ticketkit.OK(t, tr.Approve())
	stale := filepath.Join(tr.ChildTree("i0001-01-07"), "wip", "proposals", "todo", "i0001.md")
	if !ticketkit.Exists(stale) {
		t.Fatal("古い版が残っていない")
	}
	if got := pendingApproval(tr); len(got) != 0 {
		t.Fatalf("承認待ち = %v", got)
	}
	again := tr.Agree("y")
	ticketkit.Lacks(t, again.Stdout, "design")
	held := ticketkit.Read(t, tr.ApprovedPath("doing", "i0001"))
	ticketkit.Lacks(t, held, "design")
	ticketkit.Has(t, held, "chores")
	lines := lintLinesWith(tr, "i0001")
	if !anyLine(lines, "計画・コピー・範囲の違う提案", ".claude/worktrees/i0001-01-07/wip/proposals/todo/i0001.md", whereToWrite) {
		t.Errorf("lint = %v", lines)
	}
}

// 改版は正とするツリーの todo/ に置く。承認済みチケットと並んでも承認待ちに残る。古い提案を持つワークツリーがあっても、正とするツリーの
// 改版は読む。
func TestARevisionInTheHomeTreeStaysWaiting(t *testing.T) {
	t.Parallel()
	tr := phaseFamily(t)
	tr.Worktree("i0001-01-07", "i0001")
	tr.Propose("i0001", parentWithPlan(tr, "research", "design", "chores"), "")
	tr.CommitParent("tickets")
	if got := pendingApproval(tr); len(got) != 1 || got[0] != "i0001" {
		t.Fatalf("承認待ち = %v", got)
	}
	if got := lintLinesWith(tr, "古い"); len(got) != 0 {
		t.Errorf("古い提案を言った: %v", got)
	}
	ticketkit.OK(t, tr.Approve())
	ticketkit.Has(t, ticketkit.Read(t, tr.ApprovedPath("doing", "i0001")), "chores")
}

// 正としないツリー（ここではワークスペースルート）に書いた改版は承認の対象にならない。何も言わずに外さず、--lint が書く場所
// （正とするツリーの todo/）を案内する。
func TestARevisionWrittenOutsideTheHomeTreeIsNamedWithWhereToWrite(t *testing.T) {
	t.Parallel()
	tr := phaseFamily(t)
	writeIn(t, tr.Root, "wip/proposals/todo/i0001.md", parentWithPlan(tr, "research", "design", "chores"))
	if got := pendingApproval(tr); len(got) != 0 {
		t.Fatalf("承認待ち = %v", got)
	}
	lines := lintLinesWith(tr, "i0001")
	if !anyLine(lines, "計画・コピー・範囲の違う提案", "wip/proposals/todo/i0001.md", whereToWrite) {
		t.Errorf("lint = %v", lines)
	}
}

// 承認済みの無い識別子（新規の提案）は今までどおり。どのツリーに在っても承認待ち。
func TestANewProposalInAWorktreeIsStillWaiting(t *testing.T) {
	t.Parallel()
	tr := phaseFamily(t)
	other := tr.Worktree("i0001-01-07", "i0001")
	writeIn(t, other, "wip/proposals/todo/i0002.md", samples.ParentText("i0002", samples.Plan("research"), samples.ParentOpts{Copy: tr.Phases}))
	if got := pendingApproval(tr); len(got) != 1 || got[0] != "i0002" {
		t.Errorf("承認待ち = %v", got)
	}
}

// 親のツリーの改版が子のワークツリーにも入っている形。承認待ちに残り、lint も言わない。
func TestAHomeRevisionCopiedIntoAWorktreeStaysWaitingAndIsNotNamed(t *testing.T) {
	t.Parallel()
	tr := phaseFamily(t)
	tr.Propose("i0001", parentWithPlan(tr, "research", "design", "chores"), "")
	tr.CommitParent("tickets")
	tr.Worktree("i0001-01-07", "i0001") // 改版の todo/ を持ったまま切る
	if got := pendingApproval(tr); len(got) != 1 || got[0] != "i0001" {
		t.Errorf("承認待ち = %v", got)
	}
	if lines := lintLinesWith(tr, "i0001-01-07/wip/proposals/todo/i0001.md"); len(lines) != 0 {
		t.Errorf("lint = %v", lines)
	}
}

// 承認済みチケットが元ツリー（ワークスペースルート）にしか無くても、それを正としない。正とするのは親のワークツリーだけ。親のワークツリーに
// 承認前の todo/ しか無ければ、それは承認待ちの提案で、元ツリーのコピーで代用して巻き戻しを避けることはしない。
func TestACopyInTheWorkspaceRootDoesNotDecideTheHomeTree(t *testing.T) {
	t.Parallel()
	tr := phaseFamily(t)
	held := tr.ApprovedPath("doing", "i0001")
	text := ticketkit.Read(t, held)
	mustRemove(t, held)
	ticketkit.Write(t, filepath.Join(tr.Root, ".ccnavi", "approved", "doing", "i0001.md"), text)
	tr.Propose("i0001", parentWithPlan(tr, "research"), "") // 承認前の古い版
	tr.CommitParent("tickets")
	if got := pendingApproval(tr); len(got) != 1 || got[0] != "i0001" {
		t.Errorf("承認待ち = %v", got)
	}
}

// クローズした識別子を別のツリーで提案したとき、--lint は前と同じ再開の案内を出す。
func TestAClosedIdentifierProposedInAnotherTreeKeepsTheReopenGuidance(t *testing.T) {
	t.Parallel()
	tr := phaseFamily(t)
	ticketkit.Write(t, filepath.Join(tr.Root, ".ccnavi", "approved", "done", "i0005.md"), samples.ParentText("i0005", samples.Plan("research"), samples.ParentOpts{Copy: tr.Phases}))
	tr.Propose("i0005", samples.ParentText("i0005", samples.Plan("research", "design"), samples.ParentOpts{Copy: tr.Phases}), "")
	tr.CommitParent("tickets")
	lines := lintLinesWith(tr, "i0005")
	if !anyLine(lines, "i0005 はクローズしたかレビュー待ちなのに todo/ にも在る", "再開するには、ユーザが承認済みチケットを戻す") {
		t.Errorf("lint = %v", lines)
	}
	if got := pendingApproval(tr); len(got) != 0 {
		t.Errorf("承認待ち = %v", got)
	}
}

// 承認待ちに入らない改版を --verify と --agree が名指しする。
func verifyNamesWhereToWrite(t *testing.T, tr *ticketkit.Tree, placeRel string) {
	t.Helper()
	if got := pendingApproval(tr); len(got) != 0 {
		t.Fatalf("承認待ち = %v", got)
	}
	verify := ticketkit.Fails(t, tr.Ccnavi("--agree", "--preview", "--verify"))
	ticketkit.Has(t, verify.Stdout, "承認待ちに入らない改版がある")
	ticketkit.Has(t, verify.Stdout, placeRel)
	// 改版そのものができないと読める一文は出さない。同じ名指しを標準エラーに重ねない。
	ticketkit.Lacks(t, verify.Stdout, "同じ名前で置いても承認待ちにはならない")
	ticketkit.Lacks(t, verify.Stderr, placeRel)
	ticketkit.Has(t, verify.Stdout, whereToWrite)
	agree := tr.Agree("y")
	ticketkit.Has(t, agree.Stdout, "承認待ちのチケットはありません。")
	ticketkit.Has(t, agree.Stderr, placeRel)
	ticketkit.Has(t, agree.Stderr, whereToWrite)
}

// 正としないツリー（ワークスペースルート・子のワークツリー）の改版は承認待ちに入れず、--agree と --verify がファイルの場所と書くツリーを名指しする。
func TestAgreeNamesARevisionWrittenOutsideTheHomeTree(t *testing.T) {
	t.Parallel()
	t.Run("workspace_root", func(t *testing.T) {
		t.Parallel()
		tr := phaseFamily(t)
		writeIn(t, tr.Root, "wip/proposals/todo/i0001.md", parentWithPlan(tr, "research", "design", "chores"))
		verifyNamesWhereToWrite(t, tr, "wip/proposals/todo/i0001.md")
	})
	t.Run("child_worktree", func(t *testing.T) {
		t.Parallel()
		tr := phaseFamily(t)
		child := tr.Worktree("i0001-01-01", "i0001")
		writeIn(t, child, "wip/proposals/todo/i0001.md", parentWithPlan(tr, "research", "design", "chores"))
		verifyNamesWhereToWrite(t, tr, ".claude/worktrees/i0001-01-01/wip/proposals/todo/i0001.md")
	})
}

// 正とするツリーに計画の違う別の改版が在るとき。改版は承認待ちに残り、子のワークツリーに残った古い版は名指しする。
func TestAnOldCopyIsNamedWhileAnotherRevisionWaitsInTheHomeTree(t *testing.T) {
	t.Parallel()
	tr := phaseFamily(t)
	tr.Propose("i0001", parentWithPlan(tr, "research", "design", "chores"), "")
	tr.CommitParent("tickets")
	tr.Worktree("i0001-01-07", "i0001")                               // 改版 1 を持ったまま切る
	tr.Propose("i0001", parentWithPlan(tr, "research", "chores"), "") // 改版 2
	tr.CommitParent("tickets")
	if got := pendingApproval(tr); len(got) != 1 || got[0] != "i0001" {
		t.Fatalf("承認待ち = %v", got)
	}
	lines := lintLinesWith(tr, "i0001-01-07/wip/proposals/todo/i0001.md")
	if len(lines) != 1 {
		t.Fatalf("lint = %v", lines)
	}
	ticketkit.Has(t, lines[0], "計画・コピー・範囲の違う提案")
}

// ---- 取り込み済みの親子のチケットで、正とするツリーの外に書いた改版の案内

func revisionInRoot(tr *ticketkit.Tree) string {
	tr.T.Helper()
	writeIn(tr.T, tr.Root, "wip/proposals/todo/i0001.md", samples.ParentText("i0001", samples.Plan("research", "design", "chores"), samples.ParentOpts{Copy: tr.Phases}))
	return tr.Ccnavi("--agree", "--preview", "--verify").Stdout
}

func TestASettledFamilySaysToPushFromTheParentWorktree(t *testing.T) {
	t.Parallel()
	tr := authority(t)
	verify := revisionInRoot(tr)
	ticketkit.Has(t, verify, whereToWrite)
	ticketkit.Has(t, verify, "で親のブランチへ送ってから承認を頼んでください")
}

// 親子のチケットが止まっている（親のツリーの HEAD がブランチを指していない）なら、承認の手前で言っていた止まった理由と手順を同じ文面で添える。
func TestAStoppedFamilyCarriesTheReasonFamilyProblemsGives(t *testing.T) {
	t.Parallel()
	tr := authority(t)
	tr.Parent("checkout", "--quiet", "-b", "elsewhere")
	verify := revisionInRoot(tr)
	ticketkit.Has(t, verify, "親子のチケットが止まっている")
	ticketkit.Has(t, verify, "HEAD のブランチ名を変換した識別子がフォルダ名（i0001）と合わない")
	ticketkit.Has(t, verify, "承認しない")
	ticketkit.Lacks(t, verify, "で親のブランチへ送ってから")
}
