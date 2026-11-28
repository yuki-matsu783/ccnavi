package life

import (
	"os"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/ticketkit"
)

// 先行（predecessors）を承認と着手で求める。
//  1. 承認は、先行が全部 done/ に在って取り消しでないときだけ。待てば通るもの（承認待ち・作業中・レビュー待ち）と、待っても通らないもの
//     （取り消し・どこにも無い・複数の場所）を文面で分ける
//  2. ボードの承認のプレビューと --verify に、外れた理由が出る。--lint は待てば通るものを warn、待っても通らないものを error で言う
//  3. 着手（ticket start）も同じ検査で止まる
//  4. ボードの JSON のチケットに、満たしていない先行（predecessors_unmet）が載る
//  5. 続きの子（decide の「このフェーズで直す」）は、取り消した子を先行に入れない
//  6. エージェントは承認済みチケットの predecessors を書き換えて迂回できない

// 答えが「いいえ」のときの終了コード（--verify が通らない）。
const exitAnswerNo = 3

var noReview = ticketkit.FamilyOpts{Review: []bool{false, false}}

func proposeAfter(tr *ticketkit.Tree, name string, predecessors ...string) {
	tr.T.Helper()
	tr.ProposeTicket(name, ticketkit.TicketOpts{Parent: "i0001", Phase: 1, Allow: []string{"src/c/*"}, Predecessors: predecessors})
	tr.CommitParent("approval-request " + name)
}

func placed(tr *ticketkit.Tree, name string) bool {
	return ticketkit.Exists(tr.ApprovedPath("doing", name))
}

func finishAndCommit(tr *ticketkit.Tree, name string) {
	tr.T.Helper()
	ticketkit.OK(tr.T, tr.Ccnavi("ticket", "finish", name))
	tr.CommitParent("finish " + name)
}

type unmet struct {
	Ticket string `json:"ticket"`
	State  string `json:"state"`
	Label  string `json:"label"`
}

type boardEntry struct {
	Ticket            string  `json:"ticket"`
	PredecessorsUnmet []unmet `json:"predecessors_unmet"`
}

func boardEntryOf(tr *ticketkit.Tree, name string) boardEntry {
	tr.T.Helper()
	var b struct {
		Tickets []boardEntry `json:"tickets"`
	}
	jsonOf(tr.T, tr.Ccnavi("--explain", "--json").Stdout, &b)
	for _, e := range b.Tickets {
		if e.Ticket == name {
			return e
		}
	}
	tr.T.Fatalf("板に %s が無い", name)
	return boardEntry{}
}

func TestApprovalWaitsUntilThePredecessorIsDone(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, noReview)
	proposeAfter(tr, "i0001-01-03", "i0001-01-01")
	refused := ticketkit.Fails(t, tr.Approve())
	for _, w := range []string{"i0001-01-03 は承認の対象にしない", "先行 i0001-01-01 が完了していない（いまは 作業中（doing/））", "predecessors から削除して"} {
		ticketkit.Has(t, refused.Stderr, w)
	}
	if placed(tr, "i0001-01-03") {
		t.Fatal("承認の対象にしないのに置いた")
	}
	finishAndCommit(tr, "i0001-01-01") // レビュー不要のフェーズ。done/ へ動く
	ticketkit.OK(t, tr.Approve())
	if !placed(tr, "i0001-01-03") {
		t.Error("先行が done/ に入ったのに通らない")
	}
}

// 待っても満たせない・まだ満たしていない先行は、承認の文面で原因を分ける。
func TestApprovalRefusesAPredecessorThatIsNotDone(t *testing.T) {
	t.Parallel()
	for _, c := range []struct {
		name   string
		review bool
		setup  func(tr *ticketkit.Tree) (target string)
		want   []string
	}{
		// レビュー待ち（review/）は作業としては終わっているが、done/ ではないので満たさない。
		{"waiting_for_review", true, func(tr *ticketkit.Tree) string {
			finishAndCommit(tr, "i0001-01-01")
			proposeAfter(tr, "i0001-01-03", "i0001-01-01")
			return "i0001-01-03"
		}, []string{"いまは レビュー待ち（review/）"}},
		{"cancelled", false, func(tr *ticketkit.Tree) string {
			ticketkit.OK(t, tr.Ccnavi("ticket", "cancel", "i0001-01-01", "--reason", "やめた"))
			proposeAfter(tr, "i0001-01-03", "i0001-01-01")
			return "i0001-01-03"
		}, []string{"先行 i0001-01-01 は取り消し済み", "満たせない"}},
		{"missing", false, func(tr *ticketkit.Tree) string {
			proposeAfter(tr, "i0001-01-03", "i0001-01-99")
			return "i0001-01-03"
		}, []string{"先行 i0001-01-99 がどの置き場", "取り込み済みなので predecessors から"}},
		// 同じ識別子が doing/ と done/ の両方に在る（動かす途中で止まった形跡）なら満たさない。
		{"in_two_places", false, func(tr *ticketkit.Tree) string {
			finishAndCommit(tr, "i0001-01-01")
			data := ticketkit.Read(t, tr.ApprovedPath("done", "i0001-01-01"))
			ticketkit.Write(t, tr.ApprovedPath("doing", "i0001-01-01"), data)
			tr.CommitParent("stray")
			proposeAfter(tr, "i0001-01-03", "i0001-01-01")
			return "i0001-01-03"
		}, []string{"先行 i0001-01-01 が複数の場所にある", "1 つに決めて"}},
	} {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			opts := noReview
			if c.review {
				opts = ticketkit.FamilyOpts{} // 子 1 本目はレビュー要
			}
			tr := ticketkit.FamilyTree(t, opts)
			target := c.setup(tr)
			refused := ticketkit.Fails(t, tr.Approve())
			for _, w := range c.want {
				ticketkit.Has(t, refused.Stderr, w)
			}
			if placed(tr, target) {
				t.Error("承認しない提案を置いた")
			}
		})
	}
}

func TestAPredecessorApprovedInTheSameBatchIsNotDone(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	tr.ProposeTicket("i0001-01-03", ticketkit.TicketOpts{Parent: "i0001", Phase: 1, Allow: []string{"src/c/*"}})
	proposeAfter(tr, "i0001-01-04", "i0001-01-03")
	refused := tr.Approve()
	ticketkit.Has(t, refused.Stderr, "先行 i0001-01-03 が完了していない（いまは 承認待ち（todo/））")
	if !placed(tr, "i0001-01-03") || placed(tr, "i0001-01-04") {
		t.Error("置き場の残り方が合わない（先行は通り、後の子は通らないはず）")
	}
}

// 自分自身・自分の親・輪は待っても満たさない。error で原因を言う。
func TestSelfParentAndLoopsAreErrorsThatNameTheCause(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, noReview)
	proposeAfter(tr, "i0001-01-03", "i0001-01-03")
	proposeAfter(tr, "i0001-01-04", "i0001")
	proposeAfter(tr, "i0001-01-05", "i0001-01-06")
	proposeAfter(tr, "i0001-01-06", "i0001-01-05")
	refused := tr.Approve()
	for _, w := range []string{"先行 i0001-01-03 は自分自身", "先行 i0001 は自分の親", "先行 i0001-01-06 は先行を辿ると自分に戻る",
		"i0001-01-05 → i0001-01-06 → i0001-01-05", "i0001-01-06 → i0001-01-05 → i0001-01-06"} {
		ticketkit.Has(t, refused.Stderr, w)
	}
	for _, name := range []string{"i0001-01-03", "i0001-01-04", "i0001-01-05", "i0001-01-06"} {
		if placed(tr, name) {
			t.Errorf("%s を置いた", name)
		}
	}
	problems := lintProblems(tr)
	for _, fragment := range []string{"自分自身", "自分の親", "自分に戻る"} {
		hits := 0
		for _, p := range problems {
			if strings.Contains(p.Detail, fragment) {
				hits++
				if p.Severity != "error" {
					t.Errorf("%s の重さ = %s", fragment, p.Severity)
				}
			}
		}
		if hits == 0 {
			t.Errorf("--lint が %q を言わない", fragment)
		}
	}
	// クローズした先行の先は辿らない（満たしているので輪は切れている）。
	if got := boardEntryOf(tr, "i0001-01-05").PredecessorsUnmet; len(got) != 1 || got[0].State != "cycle" {
		t.Errorf("predecessors_unmet = %v", got)
	}
}

func TestBoardPreviewAndVerifyShowWhy(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	proposeAfter(tr, "i0001-01-03", "i0001-01-01")
	body := agreePreview(tr)
	if len(body.Batch) != 0 || !body.rejectedHas("先行 i0001-01-01 が完了していない") {
		t.Errorf("プレビュー = %+v", body)
	}
	verified := tr.Ccnavi("--agree", "--preview", "--verify")
	if verified.Code != exitAnswerNo {
		t.Errorf("終了コード %d（%d のはず）\n%s", verified.Code, exitAnswerNo, verified.Stdout)
	}
	ticketkit.Has(t, verified.Stdout, "外れる")
	ticketkit.Has(t, verified.Stdout, "先行 i0001-01-01")
}

func TestLintWarnsOnWaitingAndErrsOnWhatWaitingCannotFix(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	proposeAfter(tr, "i0001-01-03", "i0001-01-01")
	proposeAfter(tr, "i0001-01-04", "i0001-01-99")
	severity := func(fragment string) []string {
		var out []string
		for _, p := range lintProblems(tr) {
			if strings.Contains(p.Detail, fragment) {
				out = append(out, p.Severity)
			}
		}
		return out
	}
	if got := severity("i0001-01-03: 先行 i0001-01-01"); !slices.Equal(got, []string{"warn"}) {
		t.Errorf("待てば通る先行 = %v", got)
	}
	if got := severity("i0001-01-04: 先行 i0001-01-99"); !slices.Equal(got, []string{"error"}) {
		t.Errorf("待っても通らない先行 = %v", got)
	}
}

// ---- 着手

// handMoved は先行つきの子 i0001-01-03 を、承認を通さずに置き場へ手で動かして、ワークツリーを切る。
func handMoved(tr *ticketkit.Tree, predecessors ...string) {
	tr.T.Helper()
	tr.ProposeTicket("i0001-01-03", ticketkit.TicketOpts{Parent: "i0001", Phase: 1, Allow: []string{"src/c/*"}, Predecessors: predecessors})
	tr.HandMove("i0001-01-03", "")
	tr.Worktree("i0001-01-03", "i0001")
}

func TestStartNamesASelfPredecessor(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, noReview)
	handMoved(tr, "i0001-01-03")
	refused := ticketkit.Fails(t, tr.Ccnavi("ticket", "start", "i0001-01-03"))
	ticketkit.Has(t, refused.Stderr, "- 先行 i0001-01-03: 自分自身")
	ticketkit.Has(t, refused.Stderr, "待っても満たせない")
}

// 置き場を手で動かして承認した子は承認の検査を通らない。着手が同じ検査で止める（承認として扱うのは置き場なので、doing/ へ手で
// 動かしただけでも承認済みになる）。
func TestStartRefusesAHandMovedChildWhosePredecessorIsOpen(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, noReview)
	handMoved(tr, "i0001-01-01")
	refused := ticketkit.Fails(t, tr.Ccnavi("ticket", "start", "i0001-01-03"))
	ticketkit.Has(t, refused.Stderr, "i0001-01-03 の先行が満たされていないので開始しない")
	ticketkit.Has(t, refused.Stderr, "- 先行 i0001-01-01: 作業中（doing/）")
	ticketSh := base.ScriptCommand(tr.Root, "ccnavi-ticket.sh")
	ticketkit.Has(t, refused.Stderr, "'"+ticketSh+" finish <先行>'")
	ticketkit.Has(t, refused.Stderr, "'"+ticketSh+" cancel i0001-01-03 --reason <理由>'")
	ticketkit.Has(t, ticketkit.Read(t, tr.ApprovedPath("doing", "i0001-01-03")), `started_at: ""`)
	finishAndCommit(tr, "i0001-01-01")
	ticketkit.OK(t, tr.Ccnavi("ticket", "start", "i0001-01-03"))
}

// 承認のときは done/ だった先行をユーザが doing/ へ戻した（再開）。着手はもう一度見る。
func TestStartRefusesWhenThePredecessorWasReopenedAfterApproval(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, noReview)
	finishAndCommit(tr, "i0001-01-01")
	proposeAfter(tr, "i0001-01-03", "i0001-01-01")
	ticketkit.OK(t, tr.Approve())
	if err := os.Rename(tr.ApprovedPath("done", "i0001-01-01"), tr.ApprovedPath("doing", "i0001-01-01")); err != nil {
		t.Fatal(err)
	}
	tr.CommitParent("reopen")
	tr.Worktree("i0001-01-03", "i0001")
	refused := ticketkit.Fails(t, tr.Ccnavi("ticket", "start", "i0001-01-03"))
	ticketkit.Has(t, refused.Stderr, "先行 i0001-01-01")
}

func TestStartRefusesACancelledOrMissingPredecessorAndSaysItCannotBeMet(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	ticketkit.OK(t, tr.Ccnavi("ticket", "cancel", "i0001-01-01", "--reason", "x"))
	handMoved(tr, "i0001-01-01", "i0001-01-99")
	refused := ticketkit.Fails(t, tr.Ccnavi("ticket", "start", "i0001-01-03"))
	for _, w := range []string{"- 先行 i0001-01-01: 取り消し済み", "- 先行 i0001-01-99: どの置き場にもありません", "満たせない"} {
		ticketkit.Has(t, refused.Stderr, w)
	}
}

func TestTheBoardNamesTheUnmetPredecessors(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, noReview)
	proposeAfter(tr, "i0001-01-03", "i0001-01-01", "i0001-01-02")
	got := boardEntryOf(tr, "i0001-01-03").PredecessorsUnmet
	if len(got) != 2 || got[0] != (unmet{"i0001-01-01", "doing", "作業中（doing/）"}) || got[1].Ticket != "i0001-01-02" || got[1].State != "doing" {
		t.Errorf("predecessors_unmet = %v", got)
	}
	finishAndCommit(tr, "i0001-01-01")
	if got := boardEntryOf(tr, "i0001-01-03").PredecessorsUnmet; len(got) != 1 || got[0].Ticket != "i0001-01-02" {
		t.Errorf("1 本クローズした後 = %v", got)
	}
	// 先行を持たないチケットは空。
	if got := boardEntryOf(tr, "i0001-01-02").PredecessorsUnmet; len(got) != 0 {
		t.Errorf("先行を持たないチケット = %v", got)
	}
}

// フェーズの子の 1 本を取り消していても、続きの子はその子を先行に持たず、着手できる。
func TestAFollowupDoesNotWaitForACancelledChild(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	ticketkit.OK(t, tr.Ccnavi("ticket", "cancel", "i0001-01-02", "--reason", "x"))
	finishAndCommit(tr, "i0001-01-01")
	f := tr.Remote([]string{"i0001-01-01"})
	ticketkit.OK(t, tr.Request(f))
	f.SetThreads(ticketkit.Thread{ID: "t1", URL: "u1", Path: "src/a/x.py", Body: "ここ"})
	ticketkit.OK(t, tr.Call(ticketkit.Call{Stdin: "f\n"}, "--cwd", tr.ParentTree, "user", "reviewed", "1", "--accept-unresolved", "--result", f.Path))
	text := ticketkit.Read(t, tr.ApprovedPath("doing", "i0001-01-03"))
	ticketkit.Has(t, text, "predecessors:\n- i0001-01-01\n")
	ticketkit.Lacks(t, strings.SplitN(text, "human_review", 2)[0], "- i0001-01-02\n")
	tr.Worktree("i0001-01-03", "i0001")
	ticketkit.OK(t, tr.Ccnavi("ticket", "start", "i0001-01-03"))
}

// ---- 迂回できない

// 止められたエージェントが、承認済みチケットの predecessors を消して通る経路は無い。
func TestAnAgentCannotDropThePredecessorFromTheApprovedTicket(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, noReview)
	tr.ProposeTicket("i0001-01-03", ticketkit.TicketOpts{Parent: "i0001", Phase: 1, Allow: []string{"src/c/*"}, Predecessors: []string{"i0001-01-01"}})
	tr.HandMove("i0001-01-03", "")
	copyPath := tr.ApprovedPath("doing", "i0001-01-03")
	for _, c := range []struct {
		tool  string
		input map[string]any
	}{
		{"Edit", map[string]any{"file_path": copyPath, "old_string": "predecessors", "new_string": "x"}},
		{"Write", map[string]any{"file_path": copyPath, "content": "---\n---\n"}},
	} {
		if d := ticketkit.Decision(t, tr.Hook("PreToolUse", c.tool, tr.ParentTree, c.input)); d != "deny" {
			t.Errorf("%s = %q（deny のはず）", c.tool, d)
		}
	}
	// シェルから書く形。組み込みの保護（コアファイル）を本番と同じく有効にして打つ（このテストの道具は既定で切っている）。
	bash := func(command, permissionMode string) string {
		r := tr.CallJSON(map[string]any{"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": tr.ParentTree, "session_id": "s1",
			"tool_input": map[string]any{"command": command}, "permission_mode": permissionMode}, "--mode", "enable", "--guard-core-files", "enable")
		return ticketkit.Decision(t, r)
	}
	// リダイレクトは ccnavi ディレクトリの保護が止める。
	if d := bash("echo x > .ccnavi/approved/doing/i0001-01-03.md", ""); d != "deny" {
		t.Errorf("リダイレクト = %q", d)
	}
	// 書き込み先を読めないコマンド（sed -i）は何も言われずに通ることはない。聞ける者が居る権限モードでは Claude Code がユーザに聞き
	// （ccnavi は判定を出さない）、居なければ ccnavi が断る（judge.UndeclaredVerdict）。
	sed := "sed -i 's/predecessors.*//' .ccnavi/approved/doing/i0001-01-03.md"
	if d := bash(sed, ""); d != "" && d != "ask" && d != "deny" {
		t.Errorf("sed -i = %q", d)
	}
	if d := bash(sed, "bypassPermissions"); d != "deny" {
		t.Errorf("bypassPermissions の sed -i = %q", d)
	}
	// 承認を自分で出す経路（ターミナルの外からの --agree / --yes）も止まる。
	approve := tr.Hook("PreToolUse", "Bash", tr.ParentTree, map[string]any{"command": "ccnavi --agree --yes i0001-01-03"}, ticketkit.HookOpts{GuardTicketApproval: "enable"})
	ticketkit.Has(t, ticketkit.Reason(t, approve), "DENY_TICKET_APPROVAL_CLI")
	// サブエージェントは着手そのものを打てない。
	sub := tr.Hook("PreToolUse", "Bash", tr.ParentTree, map[string]any{"command": "sh .ccnavi/scripts/ccnavi-ticket.sh start i0001-01-03"}, ticketkit.HookOpts{AgentID: "sub-1"})
	ticketkit.Has(t, ticketkit.Reason(t, sub), "DENY_SUBAGENT_TICKET_OP")
}
