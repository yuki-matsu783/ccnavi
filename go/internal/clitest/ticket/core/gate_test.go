package core

import (
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/ticketkit"
)

// レビューで止めている間の文に添える、通る形の案内。
const exemptNote = "止めている間に通るのは、ccnavi-ticket.sh・ccnavi-review.sh・ccnavi-git.sh と " +
	"ccnavi-launcher.sh --docs を sh で実行する形だけです。" +
	"前に cd、後ろに | head -n 3 のような行数だけの head・tail を付けてもよく、" +
	"ファイル名・リダイレクト（2>&1 など）・ほかのコマンドを足すと止まります。" +
	"提案ファイルは heredoc などでは書けないので、Write・Edit で書きます。"

// 子の終わりでフェーズが終わると、知らせが出て、レビューが済むまでサブエージェントの起動とシェルが止まる。
func TestPhaseEndAnnouncesAndTheGateCloses(t *testing.T) {
	t.Parallel()
	tr := ticketkit.ClosedPhaseTree(t, ticketkit.FamilyOpts{Plan: []string{"work-mr"}})
	said := ticketkit.Reason(t, tr.Hook("PostToolUse", "Bash", tr.ParentTree, map[string]any{"command": "ls"}))
	ticketkit.Has(t, said, "フェーズ 1（MR作業） が終わりました")
	ticketkit.Has(t, said, "request")
	ticketkit.Has(t, said, "review-request --phase 1 --body-file <依頼文>")

	ticketkit.Has(t, tr.Spawn(tr.ParentTree, "次の子"), "DENY_PHASE_REVIEW")
	if rules := tr.LastRules(); len(rules) != 1 || rules[0] != "builtin:phase-review" {
		t.Errorf("rules = %v", rules)
	}
	// 繰り返しの文面は、止めた理由（レビュー待ち）の名前で言う。範囲（builtin:ticket-scope）とは言わない。
	var again string
	for range 2 {
		again = tr.Spawn(tr.ParentTree, "次の子")
	}
	ticketkit.Has(t, again, "同じ理由（builtin:phase-review）")
	ticketkit.Has(t, tr.Bash(tr.ParentTree, "ls"), "DENY_PHASE_REVIEW")
	ticketkit.Lacks(t, tr.Bash(tr.ParentTree, "sh .ccnavi/scripts/ccnavi-git.sh status"), "DENY_PHASE_REVIEW")
	// 次のフェーズの計画は通る。
	plan := tr.WriteReason(tr.ParentTree, filepath.Join(tr.ParentTree, "wip", "proposals", "todo", "i0001-01-03.md"))
	ticketkit.Lacks(t, plan, "DENY_PHASE_REVIEW")
	// main からの起動は止まらない。
	ticketkit.Lacks(t, tr.Spawn(tr.Root, "別の話"), "DENY_PHASE_REVIEW")
}

// 止めたときと終わりの知らせは sh をワークスペースルートから案内し、そのパスは通る。`.ccnavi/scripts/` はワークスペースにしか
// 無く、プロジェクトから切ったワークツリーでは相対の `sh .ccnavi/scripts/...` が届かないので、案内は絶対パスで出す。
func TestTheGateGuidesShFromTheWorkspaceRoot(t *testing.T) {
	t.Parallel()
	tr := ticketkit.ClosedPhaseTree(t, ticketkit.FamilyOpts{Plan: []string{"work-mr"}})
	reviewSh := base.ScriptCommand(tr.Root, "ccnavi-review.sh")
	said := ticketkit.Reason(t, tr.Hook("PostToolUse", "Bash", tr.ParentTree, map[string]any{"command": "ls"}))
	ticketkit.Has(t, said, reviewSh+" review-request --phase 1")
	ticketkit.Lacks(t, said, "sh .ccnavi/scripts/")

	shell := tr.Bash(tr.ParentTree, "ls")
	ticketkit.Has(t, shell, "DENY_PHASE_REVIEW")
	ticketkit.Has(t, shell, reviewSh+" review-request --phase 1")
	ticketkit.Has(t, shell, reviewSh+" confirm --phase 1")
	ticketkit.Lacks(t, shell, "sh .ccnavi/scripts/")

	// 案内どおりに打った形は、止めている間の例外にヒットする。
	ticketkit.Lacks(t, tr.Bash(tr.ParentTree, reviewSh+" review-request --phase 1 --body-file b.md"), "DENY_PHASE_REVIEW")
}

// 止めたときと終わりの知らせは、案内の 1 本を単独で打つことまで言う。付けてよいのは `cd … &&` と `| head`・`| tail` だけで、
// ほかを足すと止まる。それを言わないと、案内に従ったつもりで止まり、案内と拒否が食い違って見える。
func TestTheGateSaysTheExemptFormIsBare(t *testing.T) {
	t.Parallel()
	tr := ticketkit.ClosedPhaseTree(t, ticketkit.FamilyOpts{})
	reviewSh := base.ScriptCommand(tr.Root, "ccnavi-review.sh")
	ticketSh := base.ScriptCommand(tr.Root, "ccnavi-ticket.sh")
	said := ticketkit.Reason(t, tr.Hook("PostToolUse", "Bash", tr.ParentTree, map[string]any{"command": "ls"}))
	ticketkit.Has(t, said, exemptNote)

	guided := reviewSh + " review-request --phase 1 --body-file b.md"
	ticketkit.Lacks(t, tr.Bash(tr.ParentTree, "cd "+tr.ParentTree+" && "+guided+" | tail -3"), "DENY_PHASE_REVIEW")
	other := tr.Bash(tr.ParentTree, "cd "+tr.ParentTree+" && ls && "+guided)
	ticketkit.Has(t, other, "DENY_PHASE_REVIEW")
	ticketkit.Has(t, other, exemptNote)
	// 改版は単独なら通る。heredoc で提案を書く連結は止まり、案内に Write・Edit と単独実行が書かれている。
	ticketkit.Lacks(t, tr.Bash(tr.ParentTree, ticketSh+" revise P-1"), "DENY_PHASE_REVIEW")
	chained := tr.Bash(tr.ParentTree, ticketSh+" revise P-1 && cat > wip/proposals/todo/x.md <<'EOF'\nb\nEOF")
	ticketkit.Has(t, chained, "DENY_PHASE_REVIEW")
	ticketkit.Has(t, chained, "Write・Edit")
	// サブエージェントの起動にはシェルのパスの話をしない。
	spawn := tr.Spawn(tr.ParentTree, "次の子")
	ticketkit.Has(t, spawn, "DENY_PHASE_REVIEW")
	ticketkit.Lacks(t, spawn, exemptNote)
}

// レビューで止めている間にシェルで通す形と止める形。免除はコマンド 1 本ずつに適用し、実行役のコマンドの中で実行される
// コマンドには適用しない（適用すると、止まっている間に `env sh …ccnavi-review.sh` の形で何でも前に置ける）。
//
// 回帰: 引用の空白がコマンドの区切りと同じ目印で渡っていた間は、`-m "docs: a b"` が 3 本に分かれて免除の形にヒットせず、
// レビューの依頼そのものが止まっていた。
func TestWhatTheGateLetsThroughWhileReviewHolds(t *testing.T) {
	t.Parallel()
	tr := ticketkit.ClosedPhaseTree(t, ticketkit.FamilyOpts{})
	launcher := base.ScriptCommand(tr.Root, "ccnavi-launcher.sh")
	docs := launcher + " --docs --keyword review"
	ticketkit.Has(t, tr.Bash(tr.ParentTree, "ls"), "DENY_PHASE_REVIEW") // 止まっている前提

	for _, row := range []struct {
		name    string
		command string
		stops   bool
	}{
		// 止めている間も、資料を引く --docs は通る（次に何をするかを調べる手段）。前の cd も通る。
		{"資料を引く --docs", docs, false},
		{"前の cd", "cd . && " + docs, false},
		{"ほかのコマンドとの連結", "ls && " + docs, true},
		{"--docs 以外のオプション", strings.Split(docs, " --docs")[0] + " --lint", true},
		{"引用に空白を含む引数", `sh .ccnavi/scripts/ccnavi-git.sh commit -m "docs: a b"`, false},
		{"先頭の sh の形", "sh .ccnavi/scripts/ccnavi-review.sh confirm --phase 1", false},
		{"env の中で実行する", "env sh .ccnavi/scripts/ccnavi-review.sh confirm --phase 1", true},
		{"sudo の中で実行する", "sudo sh .ccnavi/scripts/ccnavi-review.sh confirm --phase 1", true},
	} {
		reason := tr.Bash(tr.ParentTree, row.command)
		if got := strings.Contains(reason, "DENY_PHASE_REVIEW"); got != row.stops {
			t.Errorf("%s（%s）: 止まる=%v、期待は %v\n%s", row.name, row.command, got, row.stops, reason)
		}
	}
}

// レビューの要らないフェーズは、省略の知らせが出て、レビューで止まらない。
func TestAPhaseWithoutReviewSkipsTheGate(t *testing.T) {
	t.Parallel()
	tr := ticketkit.ClosedPhaseTree(t, ticketkit.FamilyOpts{Review: []bool{false, false}})
	said := ticketkit.Reason(t, tr.Hook("PostToolUse", "Bash", tr.ParentTree, map[string]any{"command": "ls"}))
	ticketkit.Has(t, said, "省略")
	if !ticketkit.Exists(tr.PhaseMark("1.skipped")) {
		t.Error("省略の印が置かれない")
	}
	ticketkit.Lacks(t, tr.Spawn(tr.ParentTree, "次の子"), "DENY_PHASE_REVIEW")
}
