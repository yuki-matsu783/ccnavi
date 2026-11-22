package core

import (
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/ticketkit"
)

// 判定のキーはファイルの行き先。親の cwd から子のツリーへ絶対パスで書いても、子のチケットで判定される。
func TestWritesAreJudgedByWhereTheyLand(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	child := tr.ChildTree("i0001-01-01")

	inside := tr.Hook("PreToolUse", "Write", tr.ParentTree, map[string]any{"file_path": filepath.Join(child, "src", "a", "x.py")})
	ticketkit.Lacks(t, ticketkit.Reason(t, inside), "DENY_TICKET_SCOPE")
	ticketkit.OK(t, inside)

	// 親の cwd から、子のツリーの範囲外へ。子のチケットで止まる。
	outside := tr.WriteReason(tr.ParentTree, filepath.Join(child, "src", "b", "x.py"))
	ticketkit.Has(t, outside, "DENY_TICKET_SCOPE")
	ticketkit.Has(t, outside, "i0001-01-01")

	// 親のツリーは、親の範囲の中でも置き場の外を止める（作業の場ではない）。
	for _, rel := range []string{"src/z.py", "docs/z.md"} {
		ticketkit.Has(t, tr.WriteReason(child, filepath.Join(tr.ParentTree, filepath.FromSlash(rel))), "DENY_TICKET_PARENT")
	}
	ticketkit.Lacks(t, tr.WriteReason(child, filepath.Join(tr.ParentTree, "wip", "handoff", "z.md")), "DENY_TICKET")

	// main はチケットを持たない。ルールだけで判定される。
	ticketkit.Lacks(t, tr.WriteReason(tr.Root, filepath.Join(tr.Root, "docs", "z.md")), "DENY_TICKET_SCOPE")
}

// 範囲の書き方は、書いた表記のまま・大文字小文字を区別せずに読む。
//
// 回帰: ワークツリーのルートからの相対パスを normcase したパスから作っていたので、大文字小文字を区別しない機械では
// `README.md` が `readme.md` になり、`README.md` と書いた範囲に永久にヒットしなかった（実物の GitLab で出た）。
// 範囲はユーザが宣言する意図なので、機械の都合ではなく書かれたパスの意味で、どの機械でも区別しない。
// 揃えるのは表記の大小だけで、別の場所は別の場所のまま止める。
func TestScopeMatchesByWrittenSpellingAndIgnoresCaseOnEveryMachine(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{
		Allow:      []string{"src/*", "README.md", "docs/Design/*"},
		ChildAllow: []string{"README.md", "docs/Design/*"},
	})
	child := tr.ChildTree("i0001-01-01")
	for _, row := range []struct {
		name  string
		file  string
		stops bool
	}{
		{"書いたままの表記", "README.md", false},
		{"ディレクトリの大文字を含む", "docs/Design/plan.md", false},
		{"大小違いのディレクトリ", "docs/design/plan.md", false},
		{"範囲に無いファイル", "NOTES.md", true},
		{"名前の違うディレクトリ", "docs/designs/plan.md", true},
	} {
		reason := tr.WriteReason(child, filepath.Join(child, filepath.FromSlash(row.file)))
		if got := strings.Contains(reason, "DENY_TICKET_SCOPE"); got != row.stops {
			t.Errorf("%s（%s）: 止まる=%v、期待は %v\n%s", row.name, row.file, got, row.stops, reason)
		}
	}
}

// ワークツリーが無い子は着手できない。表記違いで切ったワークツリーでも、判定は承認済みチケットで行う
// （案内は表記の違いを吸収するのに判定だけ厳密だと、「有効」と言われながら権限モード任せになる）。
func TestAChildNeedsItsWorktreeAndTheNameCaseDoesNotDropTheTicket(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t)
	tr.ProposeTicket("i0001", ticketkit.TicketOpts{Allow: []string{"src/*", "wip/*"}})
	tr.ProposeTicket("i0001-01-01", ticketkit.TicketOpts{Parent: "i0001", Phase: 1, Allow: []string{"src/a/*"}})
	ticketkit.OK(t, tr.Approve())
	tr.StartParent()
	refused := ticketkit.Fails(t, tr.Ccnavi("ticket", "start", "i0001-01-01"))
	ticketkit.Has(t, refused.Stderr, "ワークツリー")

	child := tr.Worktree("I0001-01-01", "i0001")
	ticketkit.Has(t, tr.WriteReason(child, filepath.Join(child, "src", "b", "x.py")), "DENY_TICKET_SCOPE")
	ticketkit.Lacks(t, tr.WriteReason(child, filepath.Join(child, "src", "a", "x.py")), "DENY_TICKET_SCOPE")
}

// 親の範囲を超える子も承認できる。超えた項は承認画面の「編集対象としているが」に出る（判定は親の範囲で切り詰めるので、
// 承認で止める理由が無い）。
func TestAChildBeyondTheParentIsApprovedWithAWarning(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t)
	tr.ProposeTicket("i0001", ticketkit.TicketOpts{Allow: []string{"src/*", "wip/*"}})
	tr.ProposeTicket("i0001-01-01", ticketkit.TicketOpts{Parent: "i0001", Phase: 1, Allow: []string{"docs/*"}})
	tr.ProposeTicket("i0001-01-02", ticketkit.TicketOpts{Parent: "i0001", Phase: 1, Allow: []string{"src/b/*"}})
	r := ticketkit.OK(t, tr.Approve())
	ticketkit.Has(t, r.Stdout, "編集対象としているが")
	ticketkit.Has(t, r.Stdout, "`docs/*` は親 i0001 の範囲を超えている")
	ticketkit.Lacks(t, r.Stderr, "承認の対象にしない")
	for _, child := range []string{"i0001-01-01", "i0001-01-02"} {
		if !ticketkit.Exists(tr.ApprovedPath("doing", child)) {
			t.Errorf("%s が doing/ に置かれない", child)
		}
	}
}

// 子の範囲の中でも親の範囲の外は止まり、文面の `limit:` 行が親を名指しする。子自身の範囲の外は `limit:` 行を足さない
// （`scope:` 行で足りる）。
func TestAWriteBeyondTheParentIsDeniedAndNamesTheParent(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t)
	tr.ProposeTicket("i0001", ticketkit.TicketOpts{Allow: []string{"src/*", "wip/*"}})
	tr.ProposeTicket("i0001-01-01", ticketkit.TicketOpts{Parent: "i0001", Phase: 1, Allow: []string{"src/a/*", "docs/*"}})
	tr.CommitParent("tickets")
	ticketkit.OK(t, tr.Approve())
	tr.StartParent()
	child := tr.Worktree("i0001-01-01", "i0001")
	ticketkit.OK(t, tr.Ccnavi("ticket", "start", "i0001-01-01"))

	ticketkit.Lacks(t, tr.WriteReason(child, filepath.Join(child, "src", "a", "x.py")), "DENY_TICKET_SCOPE")

	beyondParent := tr.Hook("PreToolUse", "Write", child, map[string]any{"file_path": filepath.Join(child, "docs", "x.md")})
	if got := ticketkit.Decision(t, beyondParent); got != "deny" {
		t.Errorf("decision = %q", got)
	}
	reason := ticketkit.Reason(t, beyondParent)
	ticketkit.Has(t, reason, "DENY_TICKET_SCOPE")
	ticketkit.Has(t, reason, "limit: parent i0001: src/*, wip/*")

	beyondChild := tr.WriteReason(child, filepath.Join(child, "src", "b", "x.py"))
	ticketkit.Has(t, beyondChild, "DENY_TICKET_SCOPE")
	ticketkit.Lacks(t, beyondChild, "limit:")
}

// 親と子で厳しいほうが勝つ（親が ask の場所は、子が allow でも ask）。
func TestParentAndChildStrictestWins(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t)
	tr.ProposeTicket("i0001", ticketkit.TicketOpts{Allow: []string{"src/a/*"}, Ask: []string{"src/b/*"}})
	tr.ProposeTicket("i0001-01-01", ticketkit.TicketOpts{Parent: "i0001", Phase: 1, Allow: []string{"src/a/*", "src/b/*"}})
	ticketkit.OK(t, tr.Approve())
	child := tr.Worktree("i0001-01-01", "i0001")
	r := tr.Hook("PreToolUse", "Write", child, map[string]any{"file_path": filepath.Join(child, "src", "b", "x.py")})
	if got := ticketkit.Decision(t, r); got != "ask" {
		t.Errorf("decision = %q", got)
	}
	ticketkit.Has(t, ticketkit.Reason(t, r), "TICKET_ASK")
}

// 承認後に同じ識別子の提案を todo/ に書き足しても、判定に使われるのは承認済みチケット。
func TestEditingTheProposalDoesNotWidenTheScope(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	child := tr.ChildTree("i0001-01-01")
	tr.Propose("i0001-01-01", ticketkit.TicketText("i0001-01-01", ticketkit.TicketOpts{Parent: "i0001", Phase: 1, Allow: []string{"src/a/*", "src/b/*"}}), "")
	ticketkit.Has(t, tr.WriteReason(child, filepath.Join(child, "src", "b", "x.py")), "DENY_TICKET_SCOPE")
}
