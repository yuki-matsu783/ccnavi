package life

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// レビュー依頼までの道の途中で終えようとしたときの促し。止めるのは、承認済みの親に作業中の子が残っているか、子が全部閉じたのに
// （レビューを要する）フェーズの依頼のマーカー（requested）が無いとき。同じ状態では 1 回だけ、進めばまた 1 回。

func askedRequest(t *testing.T, r run.Result) string {
	t.Helper()
	reason := asked(t, r, codeRequest)
	ticketkit.Has(t, reason, "[ccnavi] "+codeRequest+" (parent: i0001, phase: 1)")
	return reason
}

// closeChildren は子 2 本を閉じる。work なら先に子 1 にコミットを置く（親にまだ合流していない子）。
func closeChildren(tr *ticketkit.Tree, work bool) {
	tr.T.Helper()
	if work {
		commitWork(tr, tr.ChildTree(childName), "src/a/work.py")
	}
	for _, child := range []string{"i0001-01-01", "i0001-01-02"} {
		ticketkit.OK(tr.T, tr.Ccnavi("ticket", "finish", child))
	}
}

func TestOpenChildrenAreAskedToFinishOnceFromTheParent(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	reason := askedRequest(t, stop(tr, tr.ParentTree))
	ticketSh := base.ScriptCommand(tr.Root, "ccnavi-ticket.sh")
	for _, w := range []string{"i0001-01-01, i0001-01-02", "`" + ticketSh + " finish i0001-01-01`", "`" + ticketSh + " finish i0001-01-02`",
		// 前置きと同じ考え方の 3 点: 許可は要らない・打つコマンド・本当の問いは書き直す。
		"ユーザの許可は要りません", "問いがある場合は、その問いを最後に書き直して", "このまま進めずにターンを終えてください"} {
		ticketkit.Has(t, reason, w)
	}
	// 同じ状態では 1 回だけ。連鎖の 2 回目も止めない。
	quiet(t, stop(tr, tr.ParentTree), codeRequest)
	quiet(t, stop(tr, tr.ParentTree, stopOpts{Active: true}), codeRequest)
	// 別のセッションは自分の記録を持つ。
	askedRequest(t, stop(tr, tr.ParentTree, stopOpts{Session: "s2"}))
}

// 並行して動く別の子は別のセッションの作業。子のツリーでは自分の子だけを言う。
func TestAChildWorktreeIsAskedAboutItsOwnChildOnly(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	reason := askedRequest(t, stop(tr, tr.ChildTree(childName)))
	ticketkit.Has(t, reason, "子 i0001-01-01 が残っています")
	ticketkit.Lacks(t, reason, "i0001-01-02")
}

// 信頼できない承認済みの親（手で書き換えた workflow:）には促さない。
func TestARequestNudgeIsNotGivenToABlockedParent(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	path := tr.ApprovedPath("doing", "i0001")
	text := ticketkit.Read(t, path)
	forged := "workflow: {order: dag, waits: {}, review_at: {}}"
	ticketkit.Write(t, path, strings.Replace(text, "\n---\n", "\n"+forged+"\n---\n", 1))
	tr.CommitParent("forge")
	blocked := tr.Hook("PreToolUse", "Write", tr.ParentTree, map[string]any{"file_path": "src/x.py"})
	ticketkit.Has(t, ticketkit.Reason(t, blocked), "DENY_TICKET_BLOCKED")
	quiet(t, stop(tr, tr.ParentTree), codeRequest)
}

// 親のワークツリーが決まらない親は促さない。Stop のルールは続けて動く。
func TestAParentWhoseWorktreeIsNotSettledIsLeftOutAndTheRuleStillRuns(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	addStopRule(tr)
	closeChildren(tr, true)
	// 親のワークツリーの HEAD がブランチ名と合わず、親のワークツリーが決まらない形にする。
	tr.Parent("checkout", "--quiet", "-b", "elsewhere")
	quiet(t, stop(tr, tr.Root), codeRequest) // ルールの 1 回目（渡さない回）
	r := stop(tr, tr.ParentTree)
	ticketkit.Lacks(t, r.Stdout, "internal-error")
	ticketkit.Lacks(t, r.Stdout, codeRequest)
	ticketkit.Has(t, r.Stdout, codeStopRule)
}

func TestARequestNudgeByEventAndMode(t *testing.T) {
	t.Parallel()
	t.Run("subagent_stop_is_not_the_place", func(t *testing.T) {
		t.Parallel()
		tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
		quiet(t, stop(tr, tr.ParentTree, stopOpts{Event: "SubagentStop", AgentID: "a1"}), codeRequest)
		quiet(t, stop(tr, tr.ParentTree, stopOpts{AgentID: "a1"}), codeRequest)
		// 見送った回は数えない。メインの終わりでは止める。
		askedRequest(t, stop(tr, tr.ParentTree))
	})
	t.Run("ticket_control_and_mode_disabled", func(t *testing.T) {
		t.Parallel()
		tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
		quiet(t, stop(tr, tr.ParentTree, stopOpts{Extra: []string{"--ticket-control", "disable"}}), codeRequest)
		quiet(t, stop(tr, tr.ParentTree, stopOpts{Mode: "disable"}), codeRequest)
	})
	t.Run("dry_run_blocks_too", func(t *testing.T) {
		t.Parallel()
		tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
		r := stop(tr, tr.ParentTree, stopOpts{Mode: "dry-run"})
		b := body(t, r)
		if !blocks(b) {
			t.Fatalf("引き止めていない: %s", r.Stdout)
		}
		ticketkit.Has(t, reasonOf(b), codeRequest)
		ticketkit.Lacks(t, r.Stdout, "would have blocked")
	})
}

// 記録を残せないまま止めると、終えるたびに止まる。止めない側を採る。
func TestARecordOrAStateDirectoryThatCannotBeUsedMeansNoNudge(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	// 記録のファイルの場所をディレクトリで塞ぐ。
	if err := os.MkdirAll(nudgePath(tr, "s1"), 0o755); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 2; i++ {
		r := stop(tr, tr.ParentTree)
		quiet(t, r, codeRequest)
		ticketkit.Has(t, r.Stderr, "促さない")
	}
	if err := os.RemoveAll(tr.State); err != nil {
		t.Fatal(err)
	}
	ticketkit.Write(t, tr.State, "ファイルで塞ぐ\n")
	quiet(t, stop(tr, tr.ParentTree), codeRequest)
}

// 促した（チケット, HEAD）の記録。セッションごとに置く。
func nudgePath(tr *ticketkit.Tree, session string) string {
	return filepath.Join(tr.State, "nudged-"+session+".json")
}

// 実行中のサブエージェントが居る間は、委任して待つ回なので止めない。
func TestARequestNudgeIsHeldByARunningSubagent(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	holdsWhileASubagentRuns(tr, tr.ParentTree, func() run.Result { return stop(tr, tr.ParentTree) }, codeRequest)
}

func TestEveryRunningSubagentMustFinishIncludingNestedOnes(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	startAgent(tr, "a1", tr.ParentTree)
	startAgent(tr, "a1-child", tr.ChildTree(childName))
	ticketkit.OK(t, stopAgent(tr, "a1", tr.ParentTree))
	quiet(t, stop(tr, tr.ParentTree), codeRequest)
	ticketkit.OK(t, stopAgent(tr, "a1-child", tr.ChildTree(childName)))
	askedRequest(t, stop(tr, tr.ParentTree))
}

// キーが決まらないので印は置かない。その体は数えられず、促しが出うる（制限）。
func TestAStartWithoutAnAgentIdLeavesNoMark(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	ticketkit.OK(t, tr.Hook("SubagentStart", "", tr.ParentTree, nil))
	for _, n := range listDir(tr.State) {
		if strings.HasSuffix(n, ".running") {
			t.Errorf("印を置いた: %s", n)
		}
	}
	askedRequest(t, stop(tr, tr.ParentTree))
}

// 起動と再開は新しいプロセスで、前の体は生きていない。compact と clear は同じプロセス。
func TestStartupAndResumeDropTheMarksButCompactAndClearKeepThem(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	sessionStart := func(source string) {
		ticketkit.OK(t, tr.CallJSON(map[string]any{"hook_event_name": "SessionStart", "session_id": "s1", "source": source}, "--mode", "enable"))
	}
	for _, source := range []string{"compact", "clear"} {
		startAgent(tr, "a1", tr.ParentTree)
		sessionStart(source)
		if !ticketkit.Exists(runningMark(tr, "a1", "")) {
			t.Errorf("%s で印が消えた", source)
		}
		quiet(t, stop(tr, tr.ParentTree), codeRequest)
	}
	for _, source := range []string{"startup", "resume"} {
		startAgent(tr, "a1", tr.ParentTree)
		sessionStart(source)
		if ticketkit.Exists(runningMark(tr, "a1", "")) {
			t.Errorf("%s で印が残った", source)
		}
		askedRequest(t, stop(tr, tr.ParentTree))
		// 次の回の前提を揃える（促しの記録を破棄する）。
		mustRemove(t, nudgePath(tr, "s1"))
	}
}

// 範囲外の変更で差し戻した回は続けさせるので印を残す。2 度目に終わったときに外す。
func TestABouncedSubagentKeepsItsMarkAndTheBounceStillWorks(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	child := tr.ChildTree(childName)
	startAgent(tr, "sub-1", child)
	writeIn(t, child, "src/b/outside.py", "x\n")
	first := stopAgent(tr, "sub-1", child)
	if first.Code != 2 {
		t.Fatalf("差し戻しの終了コード %d\n%s%s", first.Code, first.Stdout, first.Stderr)
	}
	ticketkit.Has(t, first.Stderr, "POST_TICKET_SCOPE")
	if !ticketkit.Exists(runningMark(tr, "sub-1", "")) {
		t.Error("差し戻した回に印が消えた")
	}
	ticketkit.OK(t, stopAgent(tr, "sub-1", child))
	if ticketkit.Exists(runningMark(tr, "sub-1", "")) {
		t.Error("2 度目に印が残った")
	}
}

// 1 回の Stop フック呼び出しで、引き止める理由は 1 つだけ。finish の促しで止めた状態は、この促しも止めない。
func TestTheFinishNudgeGoesFirstAndTheSameStateIsNotAskedAgain(t *testing.T) {
	t.Parallel()
	tr, tree := familyWithCommit(t)
	first := body(t, stop(tr, tree))
	ticketkit.Has(t, reasonOf(first), codeFinish)
	ticketkit.Lacks(t, reasonOf(first), codeRequest)
	quiet(t, stop(tr, tree), codeFinish)
	quiet(t, stop(tr, tree), codeRequest)
}

func TestTheRequestNudgeGoesBeforeTheStopRuleAndTheRuleIsNotCounted(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	addStopRule(tr)
	first := body(t, stop(tr, tr.ParentTree))
	ticketkit.Has(t, reasonOf(first), codeRequest)
	ticketkit.Lacks(t, reasonOf(first), codeStopRule)
	// ここからルールを数える（1 回目は止めない、2 回目で渡す）。
	quiet(t, stop(tr, tr.ParentTree), codeRequest)
	second := body(t, stop(tr, tr.ParentTree))
	if !strings.HasPrefix(reasonOf(second), codeStopRule+": ") {
		t.Errorf("2 回目 = %v", second)
	}
	ticketkit.Lacks(t, reasonOf(second), codeRequest)
}

func TestTheRequestNudgeIsRecordedApartFromDeny(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	stop(tr, tr.ParentTree)
	line := lastDecisionLine(tr)
	if line["decision"] != "nudge" || line["code"] != codeRequest || line["enforced"] != true {
		t.Errorf("記録 = %v", line)
	}
}

// ---- 計画を持つ親。フェーズのレビューの要否（延期）と、実際のレビューの状態を作って確かめる。

// closeFirstChild は計画の 1 番目の子を閉じて親に合流する。親のツリーで子は全部閉じた形になる。
func closeFirstChild(t *testing.T, plan []samples.Step, allow []string, rel string) *ticketkit.Tree {
	t.Helper()
	key := fmt.Sprintf("close-first-child %#v %v %s", plan, allow, rel)
	return ticketkit.Cached(t, key, ticketkit.Options{Phases: samples.Phases}, func(tr *ticketkit.Tree) {
		tr.PhaseFamily(plan)
		tr.Propose(childName, samples.ChildText(childName, "i0001", 1, allow), "")
		tr.CommitParent("tickets")
		ticketkit.OK(tr.T, tr.Approve())
		tr.RunChild(childName, ticketkit.File{Path: rel, Text: "x\n"})
		ticketkit.OK(tr.T, tr.Ccnavi("ticket", "finish", childName))
		tr.CommitParent("close 01")
		tr.Merge(childName)
	})
}

func TestAPlannedParentAsksForAReviewPhaseAsAControlAndNotForADeferredOne(t *testing.T) {
	t.Parallel()
	for _, c := range []struct {
		name string
		plan []samples.Step
		want bool
	}{
		{"a_merge_request_phase_is_asked", samples.Plan("design", "implement"), true},
		// 延期したフェーズは依頼できない（次にレビューのあるフェーズと一緒に見る）。
		{"a_deferred_phase_is_not_asked", []samples.Step{samples.DeferStep("design"), {Type: "implement"}, {Type: "acceptance"}}, false},
	} {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			tr := closeFirstChild(t, c.plan, []string{"wip/design/*"}, "wip/design/p.md")
			b := body(t, stop(tr, tr.ParentTree))
			if c.want {
				if !blocks(b) {
					t.Fatalf("引き止めていない: %v", b)
				}
				ticketkit.Has(t, reasonOf(b), codeRequest)
			} else if _, ok := b["decision"]; ok {
				t.Errorf("引き止めた: %v", b)
			}
		})
	}
}

// 依頼したあと、レビュアーが変更要求を付けた。confirm は通らず、requested が残る。
func TestARealChangeRequestKeepsTheRequestedMarkAndIsNotAsked(t *testing.T) {
	t.Parallel()
	tr := closeFirstChild(t, samples.Plan("design", "implement"), []string{"wip/design/*"}, "wip/design/p.md")
	f := tr.Origin()
	ticketkit.OK(t, tr.Request(f))
	if !ticketkit.Exists(tr.RequestedMark()) {
		t.Fatal("依頼のマーカーが無い")
	}
	f.SetReviews(ticketkit.Review{State: "CHANGES_REQUESTED", URL: "r/1", SubmittedAt: "2026-01-02T00:00:00Z", Author: "9001"})
	f.SetThreads()
	refused := ticketkit.Fails(t, tr.Confirm(f))
	ticketkit.Has(t, refused.Stderr, "変更要求")
	if !ticketkit.Exists(tr.RequestedMark()) || ticketkit.Exists(tr.PhaseMark("1.reviewed")) {
		t.Error("マーカーの状態が合わない")
	}
	if b := body(t, stop(tr, tr.ParentTree)); b["decision"] != nil {
		t.Errorf("引き止めた: %v", b)
	}
}
