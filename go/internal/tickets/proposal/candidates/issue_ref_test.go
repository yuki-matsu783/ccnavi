package candidates

import (
	"strings"
	"testing"

	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/testkit"
)

// 改版で課題を別のリポジトリへ移せない（番号が同じでも `owner/repo#N` のリポジトリが違えば断る）。
func TestARevisionCannotMoveTheIssueToAnotherRepository(t *testing.T) {
	root := candidatesWorkspace(t)
	conf := testkit.Settings(t, root)
	cfg := phases.LoadTypes(conf, root, "")
	parse := func(text string) *ticket.Ticket {
		found, problems := ticket.Parse(text)
		if found == nil {
			t.Fatal(problems)
		}
		return found
	}
	text := testkit.ProposalParent("i0002", plan3[:2])
	current := parse(text)
	moved := parse(strings.Replace(text, "issue: 1\n", "issue: acme/other#1\n", 1))
	if moved.IssueRepo != "acme/other" || moved.Issue != current.Issue {
		t.Fatalf("見本が違う: %q %d", moved.IssueRepo, moved.Issue)
	}
	said := ""
	for _, p := range revisionProblems(conf, root, moved, current, cfg) {
		said += p.Detail + "\n"
	}
	if !strings.Contains(said, "タイトルかissue番号が承認済みチケットと違う") {
		t.Errorf("課題のリポジトリを変える改版を通した: %q", said)
	}
}
