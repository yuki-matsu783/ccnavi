package core

import (
	"path/filepath"
	"testing"

	"ccnavi/internal/harness/ticketkit"
)

// ユーザの入口（承認・承認の push・ボードの判断・放棄の記録・ターミナル要求を切る形）は、エージェントのシェルから打たせない。
// 保護を切れば適用しない。
//
// hook を通した経路の代表と、止めた文の要点だけを見る。どの表記を止め、どれを通すかの表（分け書き・展開・glob・流し込み・実行役・
// PowerShell・大文字小文字）は、ここで打っていた形を集めた Go の見本（go/internal/hook/judge/testdata/captured.jsonl）と
// useronly の表が確かめる。
func TestUserEntriesAreDeniedFromTheShellUnlessDisabled(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	const review = ".ccnavi/scripts/ccnavi-review.sh"
	enable := ticketkit.HookOpts{GuardTicketApproval: "enable"}
	for _, row := range []struct{ tool, command, needle string }{
		{"Bash", "ccnavi --agree", ""},
		{"Bash", "ccnavi --agree --yes i0001,i0001-01-01 --json", ""},
		{"Bash", "sh .ccnavi/scripts/ccnavi-user-push-approved.sh", "ccnavi-user-push-approved.sh"},
		{"Bash", "sh .ccnavi/scripts/CCNAVI-USER-AGREE.sh", ""},
		{"Bash", "sh " + review + " decide 1 --choices j --digest d", ""},
		{"Bash", "sh " + review + ` decide 3 "$C"`, "そのまま書く"},
		{"Bash", "ccnavi worktree $V --abandon P1", "worktree retire --abandon"},
		{"Bash", "ccnavi --preview $X", "展開を含む語より前に書けば"},
		{"Bash", `echo "ccnavi worktree retire --abandon i1" | sh`, "標準入力から読むシェル"},
		{"Bash", "CCNAVI_GUARD_TICKET_APPROVAL=disable ccnavi --agree", "ターミナルからの入力を求めないようにする形"},
		{"PowerShell", "ccnavi --agree --yes i0001 | findstr --preview", ""},
	} {
		reason := tr.Judge(row.tool, tr.ParentTree, map[string]any{"command": row.command}, enable)
		ticketkit.Has(t, reason, "DENY_TICKET_APPROVAL_CLI")
		ticketkit.Has(t, reason, row.needle)
		// ボードは Enter まで送るので、ユーザが確かめる前提の文は合わない。
		ticketkit.Lacks(t, reason, "ユーザが確かめて実行します")
	}
	// 読むだけの形と、スクリプト経由の作業は通る。
	for _, command := range []string{
		"ccnavi --explain",
		"sh .ccnavi/scripts/ccnavi-ticket.sh finish i0001-01-01",
		"ccnavi --agree --preview --json",
		"sh " + review + " decide 1",
		"echo ccnavi --agree --yes x",
	} {
		ticketkit.Lacks(t, tr.Judge("Bash", tr.ParentTree, map[string]any{"command": command}, enable), "DENY_TICKET_APPROVAL_CLI")
	}
	// 保護そのものを切っていれば適用しない（テストと CI の設定）。
	ticketkit.Lacks(t, tr.Bash(tr.ParentTree, "ccnavi --agree"), "DENY_TICKET_APPROVAL_CLI")
}

// 承認と「レビュー済み」の記録は、保護を切っていなければターミナルからの入力が要る（sh の入口からは通らない）。
func TestApproveAndReviewedNeedATerminalUnlessDisabled(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t)
	tr.ProposeTicket("i0001", ticketkit.TicketOpts{Allow: []string{"src/*", "wip/*"}})
	refused := ticketkit.Fails(t, tr.Call(ticketkit.Call{Stdin: "y\n"}, "--entry", "sh", "user", "agree", "--guard-ticket-approval", "enable"))
	ticketkit.Has(t, refused.Stderr, "ターミナル")
	if ticketkit.Exists(tr.ApprovedPath("doing", "i0001")) {
		t.Error("承認された")
	}
	refused = ticketkit.Fails(t, tr.Call(ticketkit.Call{Stdin: "y\n"},
		"--cwd", tr.ParentTree, "--entry", "sh", "user", "reviewed", "1", "--guard-ticket-approval", "enable"))
	ticketkit.Has(t, refused.Stderr, "ターミナル")
	ticketkit.OK(t, tr.Approve())
}

// --lint は、運用のすき間を名指しする（チケットが無いワークツリー・保護を切る設定・登録していない hook・origin の無さ）。
// 親の範囲を超えている子は、承認の前に名指しする。--explain はコピーとフェーズを並べる。
func TestLintNamesTheGapsAndExplainListsCopiesAndPhases(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	tr.Worktree("stray", "main")
	// PostToolUse だけ登録した設定。SubagentStop が無いことを名指しさせる。
	ticketkit.Write(t, filepath.Join(tr.Root, ".claude", "settings.json"), `{"hooks": {"PostToolUse": [{"hooks": [{"command": "ccnavi"}]}]}}`)
	lint := tr.Ccnavi("--lint", "--mode", "enable")
	ticketkit.Has(t, lint.Stdout, "stray にチケットがありません。")
	ticketkit.Has(t, lint.Stdout, "CCNAVI_GUARD_TICKET_APPROVAL=disable")
	ticketkit.Has(t, lint.Stdout, "SubagentStop")
	ticketkit.Has(t, lint.Stdout, "origin がありません。")
	// 超えている子を、承認の前に名指しする。
	tr.ProposeTicket("i0001-01-03", ticketkit.TicketOpts{Parent: "i0001", Phase: 1, Allow: []string{"docs/*"}})
	ticketkit.Has(t, tr.Ccnavi("--lint", "--mode", "enable").Stdout, "超えている")

	explain := ticketkit.OK(t, tr.Ccnavi("--explain"))
	ticketkit.Has(t, explain.Stdout, "i0001-01-01")
	ticketkit.Has(t, explain.Stdout, "フェーズ 1")
}
