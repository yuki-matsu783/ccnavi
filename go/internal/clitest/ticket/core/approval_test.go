package core

import (
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// 承認の対象を識別子で絞る（VS Code 拡張が絞り込みで見えている分だけを渡す）。
func TestApproveOnlyTheListedTickets(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t)
	tr.ProposeTicket("i0001", ticketkit.TicketOpts{Allow: []string{"src/*", "wip/*"}})
	tr.ProposeTicket("i0001-01-01", ticketkit.TicketOpts{Parent: "i0001", Phase: 1, Allow: []string{"src/a/*"}})
	other := tr.Worktree("i0002", "main")
	tr.Propose("i0002", ticketkit.TicketText("i0002", ticketkit.TicketOpts{Allow: []string{"docs/*"}}), other)

	r := ticketkit.OK(t, tr.Agree("y", "i0001", "i0001-01-01"))
	ticketkit.Has(t, r.Stdout, "承認待ち 3 件のうち、指定の 2 件")
	ticketkit.Lacks(t, strings.SplitN(r.Stdout, "チケットの承認リクエスト", 2)[1], "i0002")
	for _, name := range []string{"i0001", "i0001-01-01"} {
		if !ticketkit.Exists(tr.ApprovedPath("doing", name)) {
			t.Errorf("%s が承認されない", name)
		}
	}
	otherApproved := filepath.Join(other, ".ccnavi", "approved", "doing", "i0002.md")
	if ticketkit.Exists(otherApproved) {
		t.Error("指定しなかった i0002 まで承認された")
	}
	// 残した分は次の --agree で承認の対象に入る。承認済みチケットは提案を書いたツリー（親 i0002 のワークツリー）に置かれる
	ticketkit.OK(t, tr.Approve())
	if !ticketkit.Exists(otherApproved) {
		t.Error("次の --agree で i0002 が承認されない")
	}
}

// 識別子を並べたとき、承認できないものがあれば何も承認せず終了コード 1 で止まる（終了コードが他の承認待ちの有無で変わらない）。
func TestListedIdentifiersThatCannotBeApprovedStopEverything(t *testing.T) {
	t.Parallel()
	for _, row := range []struct {
		name     string
		propose  func(tr *ticketkit.Tree)
		ids      []string
		stderr   string
		absentID string
	}{
		{
			name: "親が承認待ちのまま子だけを並べる",
			propose: func(tr *ticketkit.Tree) {
				tr.ProposeTicket("i0001", ticketkit.TicketOpts{Allow: []string{"src/*", "wip/*"}})
				tr.ProposeTicket("i0001-01-01", ticketkit.TicketOpts{Parent: "i0001", Phase: 1, Allow: []string{"src/a/*"}})
			},
			ids: []string{"i0001-01-01"}, stderr: "親 i0001 が承認されていない", absentID: "i0001-01-01",
		},
		{
			name:    "承認待ちが空でも並べた識別子は「無い」で失敗",
			propose: func(tr *ticketkit.Tree) {},
			ids:     []string{"i0001"}, stderr: "承認待ちに無い: i0001",
		},
		{
			name: "並べた識別子に承認待ちでないものが混ざる",
			propose: func(tr *ticketkit.Tree) {
				tr.ProposeTicket("i0001", ticketkit.TicketOpts{Allow: []string{"src/*", "wip/*"}})
			},
			ids: []string{"i0001", "i0009"}, stderr: "承認待ちに無い: i0009", absentID: "i0001",
		},
	} {
		t.Run(row.name, func(t *testing.T) {
			t.Parallel()
			tr := ticketkit.New(t)
			row.propose(tr)
			r := tr.Agree("y", row.ids...)
			if r.Code != 1 {
				t.Errorf("終了コード %d（1 のはず）", r.Code)
			}
			ticketkit.Has(t, r.Stderr, row.stderr)
			for _, name := range []string{"i0001", "i0001-01-01"} {
				if ticketkit.Exists(tr.ApprovedPath("doing", name)) {
					t.Errorf("%s が承認された", name)
				}
			}
		})
	}
}

func TestAGrandchildIsRefused(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t)
	tr.ProposeTicket("i0001", ticketkit.TicketOpts{Allow: []string{"src/*", "wip/*"}})
	tr.ProposeTicket("i0001-01-01", ticketkit.TicketOpts{Parent: "i0001", Phase: 1, Allow: []string{"src/a/*"}})
	tr.ProposeTicket("i0001-01-01-01-01", ticketkit.TicketOpts{Parent: "i0001-01-01", Phase: 1, Allow: []string{"src/a/*"}})
	r := tr.Approve()
	ticketkit.Has(t, r.Stderr, "2 階層")
	if ticketkit.Exists(tr.ApprovedPath("doing", "i0001-01-01-01-01")) {
		t.Error("孫が承認された")
	}
}

// 承認として扱うのは置き場。`.ccnavi/approved/` は組み込みの保護がエージェントの書き込みを止めるので、そこに在ること自体が
// ユーザの合意になる。置き場を動かしただけで承認として効き、親のツリーは範囲の中でも外でも置き場の外を止める
// （チケットが読めて効いている印は、DENY_TICKET_BLOCKED でなく DENY_TICKET_PARENT）。
func TestMovingTheFileAloneApprovesIt(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t)
	tr.ProposeTicket("i0001", ticketkit.TicketOpts{Allow: []string{"src/*"}})
	tr.HandMove("i0001", "")
	for _, rel := range []string{"src/x.py", "docs/x.md"} {
		ticketkit.Has(t, tr.WriteReason(tr.ParentTree, filepath.Join(tr.ParentTree, filepath.FromSlash(rel))), "DENY_TICKET_PARENT")
	}
}

// 親を動かさずに子だけ動かすと、子の範囲をどの親で切り詰めるかが決まらない。承認ならそこで失敗する。置き場を動かすだけの
// 進め方では判定が止める。子が自分の範囲だと言っている場所でも通さない。
func TestAChildMovedWithoutItsParentCannotWriteAnywhere(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t)
	tr.ProposeTicket("i0001", ticketkit.TicketOpts{Allow: []string{"src/*"}})
	tr.ProposeTicket("i0001-01-01", ticketkit.TicketOpts{Parent: "i0001", Phase: 1, Allow: []string{"src/a/*"}})
	tr.HandMove("i0001-01-01", "")
	child := tr.Worktree("i0001-01-01", "i0001")
	reason := tr.WriteReason(child, filepath.Join(child, "src", "a", "x.py"))
	ticketkit.Has(t, reason, "DENY_TICKET_BLOCKED")
	ticketkit.Has(t, reason, "i0001")
	ticketkit.Lacks(t, reason, "DENY_TICKET_SCOPE")
	// 範囲を適用する前に止めたので、記録の名前も範囲（builtin:ticket-scope）と分ける。
	if rules := tr.LastRules(); len(rules) == 0 || rules[0] != "builtin:ticket-blocked" {
		t.Errorf("記録の rules = %v", rules)
	}
	// 止まる場所は書き込みのときで、そこで初めて知るのは遅い。承認の画面が無い進め方では、--lint がその代わりになる。
	lint := ticketkit.Fails(t, tr.Ccnavi("--lint"))
	ticketkit.Has(t, lint.Stdout, "i0001-01-01")
	ticketkit.Has(t, lint.Stdout, "親 i0001 の承認済みチケットが作業中にありません")
}

// `project:` は宣言ではなく照合で、プロジェクトは置き場が決める。承認が突き合わせていた食い違いを、置き場を動かすだけの
// 進め方では判定が突き合わせる。
func TestAMovedTicketWhoseProjectDoesNotMatchItsPlaceCannotWrite(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t)
	tr.ProposeTicket("i0001", ticketkit.TicketOpts{Allow: []string{"src/*"}})
	text := strings.Replace(ticketkit.Read(t, tr.TodoPath("i0001")), "ticket: i0001\n", "ticket: i0001\nproject: nowhere\n", 1)
	tr.HandMove("i0001", text)
	reason := tr.WriteReason(tr.ParentTree, filepath.Join(tr.ParentTree, "src", "x.py"))
	ticketkit.Has(t, reason, "DENY_TICKET_BLOCKED")
	ticketkit.Has(t, reason, "nowhere")
}

// `start` は started_at と base_sha を一緒に書く。片方だけの形は道具を通っていないので、判定が止め、--lint が名指しする。
func TestABaseShaWithoutStartedAtIsBlocked(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t)
	tr.ProposeTicket("i0001", ticketkit.TicketOpts{Allow: []string{"src/*"}})
	tr.HandMove("i0001", samples.SetFields(ticketkit.Read(t, tr.TodoPath("i0001")), samples.Field{Key: "base_sha", Value: strings.Repeat("0", 40)}))
	reason := tr.WriteReason(tr.ParentTree, filepath.Join(tr.ParentTree, "src", "x.py"))
	ticketkit.Has(t, reason, "DENY_TICKET_BLOCKED")
	ticketkit.Has(t, reason, "base_sha")
	lint := ticketkit.Fails(t, tr.Ccnavi("--lint"))
	ticketkit.Has(t, lint.Stdout, "`started_at` が無いのに `base_sha` がある")
}

// done/ から doing/ へ手で戻す再開では、閉じるときの欄が残る。判定は止めず、--lint が warn で言う。
func TestFieldsLeftByAResumeAreWarnedButNotBlocked(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t)
	tr.ProposeTicket("i0001", ticketkit.TicketOpts{Allow: []string{"src/*"}})
	moved := samples.SetFields(ticketkit.Read(t, tr.TodoPath("i0001")),
		samples.Field{Key: "completed_at", Value: "2026-01-01T00:00:00+0000"}, samples.Field{Key: "cancel_reason", Value: "x"})
	tr.HandMove("i0001", moved)
	ticketkit.Lacks(t, tr.WriteReason(tr.ParentTree, filepath.Join(tr.ParentTree, "src", "x.py")), "DENY_TICKET_BLOCKED")
	found := lintProblems(t, tr, func(detail string) bool {
		return strings.Contains(detail, "値が残っている") && strings.Contains(detail, "i0001")
	})
	if len(found) != 1 || found[0].Severity != "warn" {
		t.Fatalf("warn が 1 件のはず: %+v", found)
	}
	ticketkit.Has(t, found[0].Detail, "`completed_at`")
	ticketkit.Has(t, found[0].Detail, "`cancel_reason`")
}

// 承認は中身を変えないので、提案に書いた値がそのまま承認済みチケットの値になる。ccnavi-ticket.sh だけが書く欄・合流した子の先頭
// （finish だけが書く。書けば合流済みを装える）・続きの子の目印（ユーザの判断だけが残す）を提案に書いたら、--lint が error で言い、
// 承認も止まる。
func TestAProposalWithAFieldOnlyTheToolsWriteIsNotApproved(t *testing.T) {
	t.Parallel()
	for _, row := range []struct {
		name     string
		change   func(text string) string
		lintWord string
		stderr   string
	}{
		{"スクリプトだけが書く欄", func(s string) string {
			return samples.SetFields(s, samples.Field{Key: "completed_at", Value: "2026-01-01T00:00:00+0000"})
		}, "スクリプト（ccnavi-ticket.sh）だけが書く欄", "だけが書く欄"},
		{"合流した子の先頭", func(s string) string {
			return samples.SetFields(s, samples.Field{Key: "merged_sha", Value: strings.Repeat("0", 40)})
		}, "", "`merged_sha`"},
		{"続きの子の目印", func(s string) string {
			return strings.Replace(s, "\n---\n", "\nfollowup_of: [i0001-01-01]\n---\n", 1)
		}, "続きの子を起こすレビューの行き先", "提案には書かない"},
	} {
		t.Run(row.name, func(t *testing.T) {
			t.Parallel()
			tr := ticketkit.New(t)
			tr.ProposeTicket("i0001", ticketkit.TicketOpts{Allow: []string{"src/*"}})
			ticketkit.Write(t, tr.TodoPath("i0001"), row.change(ticketkit.Read(t, tr.TodoPath("i0001"))))
			if row.lintWord != "" {
				found := lintProblems(t, tr, func(detail string) bool { return strings.Contains(detail, row.lintWord) })
				if len(found) != 1 || found[0].Severity != "error" {
					t.Errorf("error が 1 件のはず: %+v", found)
				}
			}
			r := ticketkit.Fails(t, tr.Approve())
			ticketkit.Has(t, r.Stderr, row.stderr)
			if ticketkit.Exists(tr.ApprovedPath("doing", "i0001")) {
				t.Error("承認された")
			}
		})
	}
}

// 手で書いた `workflow:` を効かせようとする形は、判定が止め、--lint が名指しする。
func TestAForgedRecordMovedByHandIsBlocked(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t)
	tr.ProposeTicket("i0001", ticketkit.TicketOpts{Allow: []string{"src/*"}})
	forged := "workflow: {order: dag, waits: {}, review_at: {}}"
	tr.HandMove("i0001", strings.Replace(ticketkit.Read(t, tr.TodoPath("i0001")), "\n---\n", "\n"+forged+"\n---\n", 1))
	ticketkit.Has(t, tr.WriteReason(tr.ParentTree, filepath.Join(tr.ParentTree, "src", "x.py")), "DENY_TICKET_BLOCKED")
	lint := ticketkit.Fails(t, tr.Ccnavi("--lint"))
	ticketkit.Has(t, lint.Stdout, "workflow")
}

// review/ はエージェントが書ける側。`finish` が書く completed_at が無いものは読まない。
func TestReviewReadsOnlyWhatFinishMoved(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	ticketkit.OK(t, tr.Ccnavi("ticket", "finish", "i0001-01-01"))
	review := filepath.Join(tr.ParentTree, "wip", "proposals", "review")
	finished := ticketkit.Read(t, filepath.Join(review, "i0001-01-01.md"))
	stray := samples.SetFields(strings.ReplaceAll(finished, "i0001-01-01", "i0001-01-05"), samples.Field{Key: "completed_at", Value: ""})
	ticketkit.Write(t, filepath.Join(review, "i0001-01-05.md"), stray)
	found := lintProblems(t, tr, func(d string) bool {
		return strings.Contains(d, "i0001-01-05") && strings.Contains(d, "`completed_at` がありません")
	})
	if len(found) == 0 {
		t.Error("completed_at の無い review/ のチケットを --lint が名指ししない")
	}
}

// 親が閉じた子は、親の範囲で切り詰められないので止める（判定が親を引く索引は作業中のものだけ。親を引けない子は止める側を採る）。
// ボードの JSON にも、書き込みが全部止まっていることが出る（理由が付いていないのに親を引けない子が居ないことも同じ出力から確かめる）。
func TestAChildWhoseParentIsClosedIsBlockedNotUnclipped(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t)
	// 子は親（src/*）に無い範囲を宣言する。承認は警告で通す。
	tr.ProposeTicket("i0001", ticketkit.TicketOpts{Allow: []string{"src/*"}})
	tr.ProposeTicket("i0001-01-01", ticketkit.TicketOpts{Parent: "i0001", Phase: 1, Allow: []string{"docs/*"}})
	ticketkit.OK(t, tr.Approve())
	child := tr.Worktree("i0001-01-01", "i0001")
	tr.CloseParentByHand("i0001")

	reason := tr.WriteReason(child, filepath.Join(child, "docs", "x.md"))
	ticketkit.Has(t, reason, "DENY_TICKET_BLOCKED")
	ticketkit.Has(t, reason, "i0001")

	board := ticketkit.ParseJSON(t, ticketkit.OK(t, tr.Ccnavi("--explain", "--json")).Stdout)
	records := map[string]map[string]any{}
	open := map[string]bool{}
	for _, raw := range board["tickets"].([]any) {
		rec := raw.(map[string]any)
		name := rec["ticket"].(string)
		records[name] = rec
		if copy, _ := rec["copy"].(map[string]any); copy != nil && copy["status"] == "open" {
			open[name] = true
		}
	}
	blockedReason, _ := records["i0001-01-01"]["blocked"].(string)
	ticketkit.Has(t, blockedReason, "i0001")
	for name := range open {
		parent, _ := records[name]["parent"].(string)
		blocked, _ := records[name]["blocked"].(string)
		if parent != "" && blocked == "" && !open[parent] {
			t.Errorf("%s は理由が付いていないのに親を引けない", name)
		}
	}
}

type lintProblem struct {
	Severity string `json:"severity"`
	Detail   string `json:"detail"`
}

// lintProblems は `--lint --json` の問題のうち、detail が match する物。
func lintProblems(t *testing.T, tr *ticketkit.Tree, match func(detail string) bool) []lintProblem {
	t.Helper()
	r := tr.Ccnavi("--lint", "--json")
	var out struct {
		Problems []lintProblem `json:"problems"`
	}
	if err := jsonUnmarshal(r.Stdout, &out); err != nil {
		t.Fatalf("--lint --json が読めない: %v\n%s", err, r.Stdout+r.Stderr)
	}
	var found []lintProblem
	for _, p := range out.Problems {
		if match(p.Detail) {
			found = append(found, p)
		}
	}
	return found
}
