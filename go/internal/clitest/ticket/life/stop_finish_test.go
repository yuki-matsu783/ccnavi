package life

import (
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/ticketkit"
)

// `finish` の打ち忘れを Stop で促す。促すのは、cwd のワークツリーのチケットが着手済みで、未コミットの変更が無く、基準点より先に
// コミットがあるときだけ。1 回の連鎖に 1 回（stop_hook_active）。何を除くかをここで固定する。

func familyWithCommit(t *testing.T) (*ticketkit.Tree, string) {
	t.Helper()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	tree := tr.ChildTree(childName)
	commitWork(tr, tree, "src/a/work.py")
	return tr, tree
}

// 着手済み・未コミット無し・基準点より先にコミットあり → 1 回だけ止めて finish を促す。
func TestACleanChildWithCommitsIsAskedToFinishOnce(t *testing.T) {
	t.Parallel()
	tr, tree := familyWithCommit(t)
	reason := asked(t, stop(tr, tree), codeFinish)
	ticketkit.Has(t, reason, "[ccnavi] "+codeFinish+" (ticket: i0001-01-01)")
	ticketkit.Has(t, reason, "`"+base.ScriptCommand(tr.Root, "ccnavi-ticket.sh")+" finish i0001-01-01`")
	ticketkit.Has(t, reason, "続ける理由")
	ticketkit.Has(t, reason, "コミットが 1 件")
	// 連鎖の 2 回目（Stop の hook が続けさせた後）は促さない。
	quiet(t, stop(tr, tree, stopOpts{Active: true}), codeFinish)
	// 閉じれば促さない。
	ticketkit.OK(t, tr.Ccnavi("ticket", "finish", childName))
	quiet(t, stop(tr, tree), codeFinish)
}

func TestAFinishNudgeIsHeldByARunningSubagent(t *testing.T) {
	t.Parallel()
	tr, tree := familyWithCommit(t)
	holdsWhileASubagentRuns(tr, tree, func() run.Result { return stop(tr, tree) }, codeFinish)
}

// 促しの出し方（モード・イベント）。促しは dry-run でも止める（止めないと文がモデルに届かない）。
func TestAFinishNudgeByModeAndEvent(t *testing.T) {
	t.Parallel()
	for _, c := range []struct {
		name string
		opts stopOpts
		want bool
	}{
		{"dry_run_blocks_too", stopOpts{Mode: "dry-run"}, true},
		{"ticket_control_disabled", stopOpts{Extra: []string{"--ticket-control", "disable"}}, false},
		{"mode_disable", stopOpts{Mode: "disable"}, false},
		// サブエージェントの終わりは別の手順（範囲外の差し戻し）で、促しは載せない。
		{"subagent_stop_is_not_the_place", stopOpts{Event: "SubagentStop", AgentID: "a1"}, false},
	} {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			tr, tree := familyWithCommit(t)
			r := stop(tr, tree, c.opts)
			if !c.want {
				quiet(t, r, codeFinish)
				return
			}
			b := body(t, r)
			if !blocks(b) {
				t.Fatalf("引き止めていない: %s", r.Stdout)
			}
			ticketkit.Has(t, reasonOf(b), codeFinish)
			ticketkit.Lacks(t, r.Stdout, "would have blocked")
		})
	}
}

// 促さない形を 1 つずつ固定する。strict は「引き止めもしない」、そうでなければ finish の促しだけを見る（作業中の子が残る形では、
// 依頼までの道の促しが別に止めうる）。
func TestAFinishNudgeIsNotGivenWhenTheWorkIsNotDone(t *testing.T) {
	t.Parallel()
	for _, c := range []struct {
		name   string
		strict bool
		// setup は木を整え、Stop を起こす cwd を返す。
		setup func(t *testing.T) (*ticketkit.Tree, string)
	}{
		{"untracked_file_means_not_done", false, func(t *testing.T) (*ticketkit.Tree, string) {
			tr, tree := familyWithCommit(t)
			writeIn(t, tree, "src/a/draft.py", "y\n")
			return tr, tree
		}},
		{"no_commit_beyond_the_base_point", false, func(t *testing.T) (*ticketkit.Tree, string) {
			tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
			return tr, tr.ChildTree(childName)
		}},
		{"a_ticket_that_is_not_started", true, func(t *testing.T) (*ticketkit.Tree, string) {
			tr := ticketkit.New(t)
			tr.ProposeTicket("i0001", ticketkit.TicketOpts{Allow: []string{"src/*", "wip/*"}})
			tr.ProposeTicket(childName, ticketkit.TicketOpts{Parent: "i0001", Phase: 1, Allow: []string{"src/a/*"}})
			tr.CommitParent("tickets")
			ticketkit.OK(t, tr.Approve())
			tree := tr.Worktree(childName, "i0001")
			commitWork(tr, tree, "src/a/work.py")
			return tr, tree
		}},
		// 親は finish が通る形（close_problems が空）のときだけ。開いた子があれば促さない。
		{"a_parent_with_open_children", false, func(t *testing.T) (*ticketkit.Tree, string) {
			tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
			commitWork(tr, tr.ParentTree, "src/parent.py")
			return tr, tr.ParentTree
		}},
		// レビュー準備中の親は、既存の Stop の案内（ユーザを待つ）と食い違わないよう促さない。
		{"a_parent_held_for_review", false, func(t *testing.T) (*ticketkit.Tree, string) {
			tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{Review: []bool{true, true}, Plan: []string{"work-mr"}})
			for _, child := range []string{"i0001-01-01", "i0001-01-02"} {
				ticketkit.OK(t, tr.Ccnavi("ticket", "finish", child))
			}
			tr.CommitParent("done")
			said := tr.Hook("PostToolUse", "Bash", tr.ParentTree, map[string]any{"command": "ls"})
			ticketkit.Has(t, ticketkit.Reason(t, said), "フェーズ 1（MR作業） が終わりました")
			tr.CommitParent("marks")
			return tr, tr.ParentTree
		}},
		{"the_workspace_root", true, func(t *testing.T) (*ticketkit.Tree, string) {
			tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
			return tr, tr.Root
		}},
		{"a_worktree_without_a_ticket", true, func(t *testing.T) (*ticketkit.Tree, string) {
			tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
			loose := tr.Worktree("scratch", "main")
			commitWork(tr, loose, "src/a/work.py")
			return tr, loose
		}},
		// 信頼できない承認済みチケット（親が閉じている）には終わりを勧めない。
		{"a_blocked_ticket", true, func(t *testing.T) (*ticketkit.Tree, string) {
			tr, tree := familyWithCommit(t)
			if err := mkdirRename(tr.ApprovedPath("doing", "i0001"), tr.ApprovedPath("done", "i0001")); err != nil {
				t.Fatal(err)
			}
			tr.CommitParent("closed by hand")
			return tr, tree
		}},
		{"fast_forwarding_the_parent_branch_alone_is_not_work", false, func(t *testing.T) (*ticketkit.Tree, string) {
			tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
			tree := tr.ChildTree(childName)
			commitWork(tr, tr.ParentTree, "src/parent.py")
			tr.Git(tree, "merge", "--quiet", "--ff-only", "i0001")
			return tr, tree
		}},
		// 親のブランチ（名前は親の識別子）を引けなければ、自分のコミットか決まらないので促さない。
		{"a_child_whose_parent_branch_cannot_be_found", false, func(t *testing.T) (*ticketkit.Tree, string) {
			tr, tree := familyWithCommit(t)
			tr.Parent("branch", "-m", "i0001", "renamed")
			return tr, tree
		}},
	} {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			tr, cwd := c.setup(t)
			if c.strict {
				quiet(t, stop(tr, cwd), codeFinish)
			} else {
				notNudged(t, stop(tr, cwd), codeFinish)
			}
		})
	}
}

// 索引にあるだけでも未コミット。1 回目は別の促し（依頼までの道）が止めうるので finish の促しだけを見て、それが済んだ 2 回目は何も言わない。
func TestAStagedFileIsNotDoneEither(t *testing.T) {
	t.Parallel()
	tr, tree := familyWithCommit(t)
	writeIn(t, tree, "src/a/draft.py", "y\n")
	notNudged(t, stop(tr, tree), codeFinish)
	tr.Git(tree, "add", "-A")
	quiet(t, stop(tr, tree), codeFinish)
}

// 取り込んだだけのコミットは数えない。親を --no-ff で取り込んだ（マージのコミットだけができた）子は促さず、自分のコミットを足せば
// それだけを数えて促す。
func TestMergingTheParentBranchWithAMergeCommitAloneIsNotWork(t *testing.T) {
	t.Parallel()
	tr, _ := familyWithCommit(t)
	ticketkit.OK(t, tr.Ccnavi("ticket", "finish", childName))
	other := tr.ChildTree("i0001-01-02")
	commitWork(tr, tr.ParentTree, "src/parent.py")
	tr.Git(other, "merge", "--quiet", "--no-ff", "--no-edit", "i0001")
	notNudged(t, stop(tr, other), codeFinish)
	commitWork(tr, other, "src/b/work.py")
	b := body(t, stop(tr, other))
	if !blocks(b) {
		t.Fatal("自分のコミットを足したのに促さない")
	}
	ticketkit.Has(t, reasonOf(b), "コミットが 1 件")
}

// 同じ HEAD では 1 回だけ。コミットが進めばまた 1 回。別のセッションは自分の記録を持つ。
func TestTheSameHeadIsAskedOncePerSessionAndAgainAfterANewCommit(t *testing.T) {
	t.Parallel()
	tr, tree := familyWithCommit(t)
	asked(t, stop(tr, tree), codeFinish)
	// 次のターンの終わり（stop_hook_active は偽）でも、HEAD が同じなら止めない。
	quiet(t, stop(tr, tree), codeFinish)
	quiet(t, stop(tr, tree), codeFinish)
	commitWork(tr, tree, "src/a/more.py")
	ticketkit.Has(t, asked(t, stop(tr, tree), codeFinish), "コミットが 2 件")
	quiet(t, stop(tr, tree), codeFinish)
	asked(t, stop(tr, tree, stopOpts{Session: "s2"}), codeFinish)
}

// 監査の記録では deny と数えない。種類は nudge。記録はワークスペースルートの既定の置き場に残る（--log は診断でだけ効く）。
func TestTheFinishNudgeIsRecordedApartFromDeny(t *testing.T) {
	t.Parallel()
	tr, tree := familyWithCommit(t)
	stop(tr, tree)
	line := lastDecisionLine(tr)
	if line["decision"] != "nudge" || line["code"] != codeFinish {
		t.Errorf("記録 = %v", line)
	}
}

// match: Stop のルールと重なったとき（1 回の Stop フック呼び出しでは理由を 1 つだけ出す）、finish の促しだけを出し、ルールの数えは進めない。
func TestTheFinishNudgeGoesFirstAndTheStopRuleIsNotCounted(t *testing.T) {
	t.Parallel()
	tr, tree := familyWithCommit(t)
	addStopRule(tr)
	first := body(t, stop(tr, tree))
	ticketkit.Has(t, reasonOf(first), codeFinish)
	ticketkit.Lacks(t, reasonOf(first), codeStopRule)
	// finish の促しは同じ HEAD では 1 回だけ。同じ状態を依頼までの道の促しがもう 1 回止めることも無い。ここからルールを数える
	// （1 回目は止めない）。
	quiet(t, stop(tr, tree), codeFinish)
	third := body(t, stop(tr, tree))
	if !blocks(third) || !strings.HasPrefix(reasonOf(third), codeStopRule+": ") {
		t.Fatalf("3 回目 = %v", third)
	}
	ticketkit.Has(t, reasonOf(third), "振り返る")
	ticketkit.Lacks(t, reasonOf(third), codeFinish)
}
