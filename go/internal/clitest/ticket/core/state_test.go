package core

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/ticketkit"
)

// レビュー要の子をクローズすると、承認済みチケットは doing/ から wip/proposals/review/ へ動く。動かすのはエージェント
// （承認は要らない。範囲が消える向きなので危険は増えない）。レビュー待ちの子のツリーへの書き込みは、チケット無しの扱いになる。
func TestFinishMovesAReviewedTicketToReviewWithoutApproval(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	ticketkit.OK(t, tr.Ccnavi("ticket", "finish", "i0001-01-01"))
	review := filepath.Join(tr.ParentTree, "wip", "proposals", "review", "i0001-01-01.md")
	if !ticketkit.Exists(review) {
		t.Fatal("review/ へ移らない")
	}
	for _, state := range []string{"doing", "done"} {
		if ticketkit.Exists(tr.ApprovedPath(state, "i0001-01-01")) {
			t.Errorf("%s/ に残っている", state)
		}
	}
	text := ticketkit.Read(t, review)
	// review/ で読まれる目印は finish が書く completed_at。承認の記録は書き足さない。
	ticketkit.Lacks(t, text, "ccnavi_approved:")
	if !regexp.MustCompile(`completed_at: "[^"]+"`).MatchString(text) {
		t.Errorf("completed_at が書かれない:\n%s", text)
	}
	child := tr.ChildTree("i0001-01-01")
	ticketkit.Lacks(t, tr.WriteReason(child, filepath.Join(child, "src", "b", "x.py")), "DENY_TICKET_SCOPE")
}

func TestStartRecordsTheBasePoint(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	copy := ticketkit.Read(t, tr.ApprovedPath("doing", "i0001-01-01"))
	head := strings.TrimSpace(tr.Git(tr.ChildTree("i0001-01-01"), "rev-parse", "HEAD"))
	ticketkit.Has(t, copy, head)
	ticketkit.Has(t, copy, "started_at:")
}

// 承認と着手の間で状態を動かしても、提案の本文（ユーザの覚え書き）は変わらない。
func TestMovingStateKeepsTheProposalText(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t)
	tr.ProposeTicket("i0001", ticketkit.TicketOpts{Allow: []string{"src/*", "wip/*"}})
	path := tr.TodoPath("i0001")
	ticketkit.Write(t, path, strings.Replace(ticketkit.Read(t, path), "---\n", "---\n# ユーザの覚え書き\n", 1))
	ticketkit.OK(t, tr.Approve())
	ticketkit.OK(t, tr.Ccnavi("ticket", "start", "i0001"))
	after := ticketkit.Read(t, tr.ApprovedPath("doing", "i0001"))
	ticketkit.Has(t, after, "# ユーザの覚え書き")
	ticketkit.Lacks(t, after, "ccnavi_approved:")
	ticketkit.Has(t, after, "started_at:")
	ticketkit.Has(t, after, "base_sha:")
}

// 子の着手は親が着手済みであることを前提にする（飛ばしても途中では何も問題が起きず、親をクローズするときだけが通らない。
// 止める場所を最初の子の着手に置けば、親の作業が実際に始まる時点で言える）。案内は親の start で、ワークスペースルートからの
// 絶対パス（子のワークツリーから打てるパス）。止まった回は子の着手の欄を書かない。親を着手すれば案内のとおりに通る。
func TestAChildDoesNotStartBeforeItsParent(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t)
	tr.ProposeTicket("i0001", ticketkit.TicketOpts{Allow: []string{"src/*", "wip/*"}})
	tr.ProposeTicket("i0001-01-01", ticketkit.TicketOpts{Parent: "i0001", Phase: 1, Allow: []string{"src/a/*"}})
	tr.CommitParent("tickets")
	ticketkit.OK(t, tr.Approve())
	tr.Worktree("i0001-01-01", "i0001")

	refused := ticketkit.Fails(t, tr.Ccnavi("ticket", "start", "i0001-01-01"))
	ticketkit.Has(t, refused.Stderr, "i0001-01-01 の親 i0001 が未着手")
	ticketkit.Has(t, refused.Stderr, base.ScriptCommand(tr.Root, "ccnavi-ticket.sh")+" start i0001")
	ticketkit.Lacks(t, refused.Stderr, "sh .ccnavi/scripts/")
	ticketkit.Has(t, ticketkit.Read(t, tr.ApprovedPath("doing", "i0001-01-01")), `started_at: ""`)

	ticketkit.OK(t, tr.Ccnavi("ticket", "start", "i0001"))
	ticketkit.OK(t, tr.Ccnavi("ticket", "start", "i0001-01-01"))
	tr.ProposeTicket("i0001-01-02", ticketkit.TicketOpts{Parent: "i0001", Phase: 1, Allow: []string{"src/b/*"}})
	tr.CommitParent("next")
	ticketkit.OK(t, tr.Approve())
	tr.Worktree("i0001-01-02", "i0001")
	ticketkit.OK(t, tr.Ccnavi("ticket", "start", "i0001-01-02"))
}

// 親が開いていない（クローズした・承認前・見つからない）ときの子の着手は、親の置き場を名指しして止まる。
func TestAChildDoesNotStartUnderAParentThatIsNotOpen(t *testing.T) {
	t.Parallel()
	for _, row := range []struct {
		name   string
		set    func(t *testing.T, tr *ticketkit.Tree)
		stderr []string
	}{
		{
			// doing/ から出た親は判定にも使われない。そこに子を足すのは未着手とは別の異常なので、親の start は案内せず、
			// いまの置き場を出す。
			name: "クローズした親",
			set: func(t *testing.T, tr *ticketkit.Tree) {
				tr.ProposeTicket("i0001", ticketkit.TicketOpts{Allow: []string{"src/*", "wip/*"}})
				tr.ProposeTicket("i0001-01-01", ticketkit.TicketOpts{Parent: "i0001", Phase: 1, Allow: []string{"src/a/*"}})
				tr.CommitParent("tickets")
				ticketkit.OK(t, tr.Approve())
				tr.Worktree("i0001-01-01", "i0001")
				tr.CloseParentByHand("i0001")
			},
			stderr: []string{"i0001-01-01 の親 i0001 は作業中ではない（いまは done/）"},
		},
		{
			// ユーザが子だけ置き場を動かすと起きる。start は doing/ の承認済みチケットにしか通らないので、todo/ の親にそのまま
			// start を勧めると、案内のとおりに打っても通らない。案内は承認から始める。
			name: "承認前の親",
			set: func(t *testing.T, tr *ticketkit.Tree) {
				tr.ProposeTicket("i0001", ticketkit.TicketOpts{Allow: []string{"src/*", "wip/*"}})
				tr.ProposeTicket("i0001-01-01", ticketkit.TicketOpts{Parent: "i0001", Phase: 1, Allow: []string{"src/a/*"}})
				tr.HandMove("i0001-01-01", "")
				tr.Worktree("i0001-01-01", "i0001")
			},
			stderr: []string{"i0001-01-01 の親 i0001 がまだ承認されていない（todo/）", "ccnavi-user-agree.sh"},
		},
		{
			// ユーザが子だけ置き場へ動かし、親を書き忘れた形。find は子を引けるので、親の側を引いたときの「無い」をここで言わないと、
			// ワークツリーの検査まで進んで別の話になる。
			name: "親の提案がどこにも無い",
			set: func(t *testing.T, tr *ticketkit.Tree) {
				tr.ProposeTicket("i0001-01-01", ticketkit.TicketOpts{Parent: "i0001", Phase: 1, Allow: []string{"src/a/*"}})
				tr.HandMove("i0001-01-01", "")
				tr.Worktree("i0001-01-01", "main")
			},
			stderr: []string{"i0001-01-01 の親 i0001 が見つからない"},
		},
	} {
		t.Run(row.name, func(t *testing.T) {
			t.Parallel()
			tr := ticketkit.New(t)
			row.set(t, tr)
			refused := ticketkit.Fails(t, tr.Ccnavi("ticket", "start", "i0001-01-01"))
			for _, want := range row.stderr {
				ticketkit.Has(t, refused.Stderr, want)
			}
		})
	}
}

// 開いた子がある親はクローズできない。子をクローズしてもレビューで止まっている間はクローズしない。
func TestAParentCannotCloseWhileChildrenAreOpen(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	ticketkit.Has(t, ticketkit.Fails(t, tr.Ccnavi("ticket", "finish", "i0001")).Stderr, "開いている子")
	ticketkit.Has(t, ticketkit.Fails(t, tr.Ccnavi("ticket", "cancel", "i0001", "--reason", "やめる")).Stderr, "開いている子")
	tr.ClosePhase()
	ticketkit.Has(t, ticketkit.Fails(t, tr.Ccnavi("ticket", "finish", "i0001")).Stderr, "レビュー準備中")
}

// 同じ識別子の提案が todo/ にもあっても、状態の操作は doing/ の側を相手にし、--lint が todo/ にも在ると言う。
func TestATodoWithTheSameIDAsAnApprovedTicketIsNotTheOperand(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	tr.ProposeTicket("i0001-01-01", ticketkit.TicketOpts{Parent: "i0001", Phase: 1, Allow: []string{"src/a/*"}})
	ticketkit.OK(t, tr.Ccnavi("ticket", "finish", "i0001-01-01"))
	ticketkit.Has(t, tr.Ccnavi("--lint", "--mode", "enable").Stdout, "todo/ にも在る")
}

// 同じ識別子が doing/ と done/ に在れば、どちらが正か決まらないので止める（動かした形跡が両方に残った形）。何も言わずに
// どちらかを選ぶと、クローズした記録を上書きするか、クローズしたはずのものが作業中として復活する。止めて、--lint が同じ 1 行で名指しする。
func TestATicketInTwoHomesIsNotOperatedOn(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	if err := os.MkdirAll(filepath.Join(tr.Approved, "done"), 0o755); err != nil {
		t.Fatal(err)
	}
	ticketkit.Write(t, tr.ApprovedPath("done", "i0001-01-01"), ticketkit.Read(t, tr.ApprovedPath("doing", "i0001-01-01")))
	done := ticketkit.Fails(t, tr.Ccnavi("ticket", "finish", "i0001-01-01"))
	ticketkit.Has(t, done.Stderr, "i0001-01-01 が複数の場所にある")
	for _, state := range []string{"doing", "done"} {
		if !ticketkit.Exists(tr.ApprovedPath(state, "i0001-01-01")) {
			t.Errorf("%s/ のチケットが動かされた", state)
		}
	}
	lint := ticketkit.Fails(t, tr.Ccnavi("--lint", "--mode", "enable"))
	ticketkit.Has(t, lint.Stdout, "i0001-01-01 が複数の場所にある")
}

// 親のワークツリーを片付けると、元ツリーのコピーで代用せずに止まる。承認済みチケットは親のブランチに乗り、合流すると元ツリーにも
// 入る。それでも正とするのは親のワークツリー上のチケットだけで、元ツリーや子のワークツリーのコピーは読まない。
func TestFoldingTheParentWorktreeStopsTheOperations(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	tr.Git(tr.Root, "merge", "--quiet", "--no-edit", "i0001")
	tr.Git(tr.Root, "worktree", "remove", "--force", tr.ParentTree)
	done := ticketkit.Fails(t, tr.Ccnavi("ticket", "finish", "i0001-01-02"))
	ticketkit.Has(t, done.Stderr, "親のワークツリーを切り直す")
	if ticketkit.Exists(filepath.Join(tr.Root, ".ccnavi", "approved", "done", "i0001-01-02.md")) {
		t.Error("元ツリーのコピーが動かされた")
	}
}

// 子のワークツリーに残った古いコピーは、親のワークツリーの答えを変えない（親のツリーでクローズし、子のツリーは取り込まないまま
// doing/ で止まっている形でも、親のツリーのチケットだけで決まる）。
func TestAStaleCopyInTheChildWorktreeDoesNotDecide(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	ticketkit.OK(t, tr.Ccnavi("ticket", "finish", "i0001-01-02"))
	tr.CommitParent("close i0001-01-02")
	stale := filepath.Join(tr.ChildTree("i0001-01-02"), ".ccnavi", "approved", "doing", "i0001-01-02.md")
	if !ticketkit.Exists(stale) {
		t.Fatal("子のツリーに古いコピーが残っていない")
	}
	const phrase = "複数の場所にある"
	status := tr.Ccnavi("ticket", "status", "i0001")
	ticketkit.Lacks(t, ticketkit.Out(status), phrase)
	ticketkit.Lacks(t, tr.Ccnavi("ticket", "finish", "i0001-01-02").Stderr, phrase)
	ticketkit.Lacks(t, tr.Ccnavi("--lint", "--mode", "enable").Stdout, "i0001-01-02 が"+phrase)
}

// クローズした承認済みチケットは git に入れて共有するので、コミットしたあとにワークツリーを切れば、その数だけ同じチケットができる。
// これを「複数の場所にある」で止めると、ワークツリーを 2 本持つだけでクローズしたチケットが全部 error になり、--lint が常に非ゼロで終わる。
// ワークツリーはそれぞれ別のコミットを指すので、古いツリーで doing、新しいツリーで done になるのも普通の形。
func TestACopyCarriedIntoWorktreesIsNotTwoHomes(t *testing.T) {
	t.Parallel()
	closed := "---\nversion: 1\nticket: i0002\ntitle: t\nallow:\n- {glob: \"src/*\", match: \"Write|Edit\"}\n---\n"
	for _, row := range []struct {
		name string
		set  func(t *testing.T, tr *ticketkit.Tree)
	}{
		{"クローズしたチケットを切ったツリーが抱える", func(t *testing.T, tr *ticketkit.Tree) {
			ticketkit.Write(t, filepath.Join(tr.Root, ".ccnavi", "approved", "done", "i0002.md"), closed)
			commitRoot(tr, "close i0002")
			tr.Worktree("carry-a", "main")
			tr.Worktree("carry-b", "main")
		}},
		{"クローズする前に切ったツリーは doing のまま", func(t *testing.T, tr *ticketkit.Tree) {
			approved := filepath.Join(tr.Root, ".ccnavi", "approved")
			ticketkit.Write(t, filepath.Join(approved, "doing", "i0002.md"), closed)
			commitRoot(tr, "start i0002")
			stale := tr.Worktree("older", "main")
			if err := os.MkdirAll(filepath.Join(approved, "done"), 0o755); err != nil {
				t.Fatal(err)
			}
			if err := os.Rename(filepath.Join(approved, "doing", "i0002.md"), filepath.Join(approved, "done", "i0002.md")); err != nil {
				t.Fatal(err)
			}
			commitRoot(tr, "close i0002")
			if !ticketkit.Exists(filepath.Join(stale, ".ccnavi", "approved", "doing", "i0002.md")) {
				t.Fatal("切ったツリーに doing/ のチケットが残っていない")
			}
		}},
	} {
		t.Run(row.name, func(t *testing.T) {
			t.Parallel()
			tr := ticketkit.New(t)
			row.set(t, tr)
			ticketkit.Lacks(t, tr.Ccnavi("--lint", "--mode", "enable").Stdout, "i0002 が複数の場所にある")
		})
	}
}

func commitRoot(tr *ticketkit.Tree, message string) {
	tr.Git(tr.Root, "add", "-A")
	tr.Git(tr.Root, "commit", "--quiet", "-m", message)
}

// フェーズが終わったあとに新しい子を承認すると、終わりの印（マーカー）が消える。
func TestANewChildInAnEndedPhaseClearsTheMarks(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	tr.ClosePhase()
	tr.Hook("PostToolUse", "Bash", tr.ParentTree, map[string]any{"command": "ls"})
	pending := tr.PhaseMark("1.pending")
	if !ticketkit.Exists(pending) {
		t.Fatal("終わりの印が置かれない")
	}
	tr.ProposeTicket("i0001-01-03", ticketkit.TicketOpts{Parent: "i0001", Phase: 1, Allow: []string{"src/a/*"}})
	r := ticketkit.OK(t, tr.Approve())
	ticketkit.Has(t, r.Stdout, "マーカー")
	if ticketkit.Exists(pending) {
		t.Error("終わりの印が消えない")
	}
}
