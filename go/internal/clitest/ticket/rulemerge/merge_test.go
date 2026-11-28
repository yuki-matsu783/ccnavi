package rulemerge

import (
	"fmt"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/ticketkit"
)

// verdict は実行前チェックの表の 1 マス。decision が空なら判定は見ない（権限モードに委ねる行）。
// text は文面の出所: "rule" はルールの message、"ticket" はチケットの文面、"" は文面なし。
// firstRule は記録の rules の先頭（ルールファイルのものは id だけ。空なら builtin:ticket-scope が載らない）。
type verdict struct{ decision, code, text, firstRule string }

var (
	ruleDeny    = verdict{"deny", "DENY_PATH", "rule", "rule-deny"}
	ticketScope = verdict{"deny", "DENY_TICKET_SCOPE", "ticket", "builtin:ticket-scope"}
	ticketAskV  = verdict{"ask", "TICKET_ASK", "ticket", "builtin:ticket-scope"}
	ruleAsk     = verdict{"ask", "RULE_ASK", "rule", "rule-ask"}
	passed      = verdict{"allow", "", "", "rule-allow"}
)

// columns はチケットの列。書き込み先のワークツリーと相対パス。
var columns = []struct {
	name, tree, rel string
}{
	{"deny", "child", ticketDeny},
	{"ask", "child", ticketAsk},
	{"allow", "child", ticketAllow},
	{"outside", "child", ticketOutside},
	{"no-ticket", "free", ticketAllow},
}

// expected はルールの列ごとに、チケットの列 → 期待。
var expected = map[string]map[string]verdict{
	"deny": {"deny": ruleDeny, "ask": ruleDeny, "allow": ruleDeny, "outside": ruleDeny, "no-ticket": ruleDeny},
	"ask": {
		"deny": ticketScope, "outside": ticketScope,
		"ask": ruleAsk, "allow": ruleAsk, "no-ticket": ruleAsk,
	},
	"allow": {
		"deny": ticketScope, "outside": ticketScope, "ask": ticketAskV,
		"allow": passed, "no-ticket": passed,
	},
	"silent": {
		"deny": ticketScope, "outside": ticketScope, "ask": ticketAskV,
		// チケットの範囲の中で、ルールも何も言わない。ルールの id もチケットの理由も残らない。
		"allow": {"allow", "", "", ""},
		// 権限モードに委ねる行。payload に permission_mode を入れないので、委ねた先の答えは見ない。見るのはコードだけ。
		"no-ticket": {"", "UNDECLARED", "", ""},
	},
}

// 実行前チェック。ルールの 4 列 × チケットの 5 列で、判定・理由コード・どちらの文面か・記録の欄を見る。
// ルール deny は常に最強。ask・allow はチケットが狭める。何も言わないルールは判定をチケットに任せる。
func TestPreToolUseMergesRuleAndTicketStrictestWins(t *testing.T) {
	for _, section := range []string{"deny", "ask", "allow", "silent"} {
		t.Run(section, func(t *testing.T) {
			t.Parallel()
			w := family(t, scope{Allow: []string{"src/*"}}, defaultChild)
			w.useRules(rulesFor(section))
			for _, col := range columns {
				want := expected[section][col.name]
				t.Run(col.name, func(t *testing.T) {
					tree := w.child
					if col.tree == "free" {
						tree = w.free
					}
					r := w.writeHook(tree, col.rel, hookOpts{})
					if r.Code != 0 {
						t.Fatalf("終了コード %d: %s", r.Code, r.Stderr)
					}
					checkPre(t, w, r, section, col.name, want)
				})
			}
		})
	}
}

func checkPre(t *testing.T, w *ws, r run.Result, section, column string, want verdict) {
	t.Helper()
	why := reason(t, r)
	record := w.lastRecord()
	if want.decision != "" && decision(t, r) != want.decision {
		t.Errorf("判定 = %q, want %q: %s", decision(t, r), want.decision, why)
	}
	if got, _ := record["code"].(string); got != want.code {
		t.Errorf("code = %q, want %q: %s", got, want.code, why)
	}

	ruleMessage := "RULE-" + strings.ToUpper(section) + "-MESSAGE"
	switch want.text {
	case "rule":
		ticketkit.Has(t, why, ruleMessage)
		ticketkit.Lacks(t, why, "DENY_TICKET_SCOPE")
		ticketkit.Lacks(t, why, "TICKET_ASK")
	case "ticket":
		ticketkit.Has(t, why, want.code)
		ticketkit.Lacks(t, parse(t, r).reason, ruleMessage)
	}

	rules := w.rules()
	if want.firstRule == "" {
		for _, name := range rules {
			if name == "builtin:ticket-scope" {
				t.Errorf("builtin:ticket-scope が載った: %v", rules)
			}
		}
	} else {
		first := want.firstRule
		if !strings.HasPrefix(first, "builtin:") {
			first = rulesFile + first
		}
		if len(rules) == 0 || rules[0] != first {
			t.Errorf("rules の先頭 = %v, want %q", rules, first)
		}
	}

	if want.text == "ticket" {
		// 判定を下したのが設定を持たないチケットなら、設定の欄は空。
		if src, _ := record["source"].(string); src != "" {
			t.Errorf("source = %q, want 空", src)
		}
		// 足す行は場面ごとに独立していて、重なれば（ルールを狭め、かつチケットの deny にヒットした）両方載る。
		if section == "ask" || section == "allow" {
			// チケットがルールを狭めた回は、ルールの id も記録に残し、文面が名指しする。
			found := false
			for _, name := range rules {
				found = found || name == rulesFile+"rule-"+section
			}
			if !found {
				t.Errorf("狭められたルールの id が記録に無い: %v", rules)
			}
			ticketkit.Has(t, why, fmt.Sprintf("rule: %srule-%s (%s) lets this through, but the ticket for this worktree narrows it", rulesFile, section, section))
		} else {
			ticketkit.Lacks(t, why, "rule: ")
		}
		if column == "deny" {
			ticketkit.Has(t, why, "ticket entry: deny src/deny/*")
		} else {
			ticketkit.Lacks(t, why, "ticket entry:")
		}
	}

	if section != "silent" {
		// ヒットしたルールの additionalContext は、判定を決めた側に関わらず載る。
		ticketkit.Has(t, r.Stdout, note)
	}
}

// 実行後チェック。シェルが書いたあとに報告するか、どのコードか（空は報告しない）。
func TestPostToolUseReportFollowsTheMergedVerdict(t *testing.T) {
	t.Parallel()
	w := family(t, scope{Allow: []string{"src/*"}}, defaultChild)
	for n, c := range []struct{ rule, column, prefix, code string }{
		{"deny", "deny", "src/deny", "POST_VIOLATION"},
		{"deny", "allow", "src/ok", "POST_VIOLATION"},
		{"deny", "outside", "docs", "POST_VIOLATION"},
		{"ask", "deny", "src/deny", "POST_VIOLATION"},
		{"ask", "allow", "src/ok", "POST_VIOLATION"},
		{"ask", "outside", "docs", "POST_VIOLATION"},
		{"allow", "deny", "src/deny", "POST_TICKET_SCOPE"},
		{"allow", "outside", "docs", "POST_TICKET_SCOPE"},
		{"allow", "ask", "src/ask", ""},
		{"allow", "allow", "src/ok", ""},
		{"silent", "deny", "src/deny", "POST_TICKET_SCOPE"},
		{"silent", "outside", "docs", "POST_TICKET_SCOPE"},
		{"silent", "ask", "src/ask", ""},
		{"silent", "allow", "src/ok", ""},
	} {
		t.Run(c.rule+"/"+c.column, func(t *testing.T) {
			w.useRules(rulesFor(c.rule))
			rel := fmt.Sprintf("%s/case%d.txt", c.prefix, n)
			checkPost(t, w.afterShell(w.child, rel, fmt.Sprintf("post-%d", n), hookOpts{}), rel, c.code)
		})
	}
}

// チケットの無いワークツリーの実行後チェックは、ルールだけで決まる。
func TestPostToolUseWithoutATicketFollowsTheRulesAlone(t *testing.T) {
	t.Parallel()
	w := family(t, scope{Allow: []string{"src/*"}}, defaultChild)
	for n, c := range []struct{ rule, code string }{
		{"deny", "POST_VIOLATION"}, {"ask", "POST_VIOLATION"}, {"allow", ""}, {"silent", ""},
	} {
		t.Run(c.rule, func(t *testing.T) {
			w.useRules(rulesFor(c.rule))
			rel := fmt.Sprintf("docs/free%d.txt", n)
			checkPost(t, w.afterShell(w.free, rel, fmt.Sprintf("free-%d", n), hookOpts{}), rel, c.code)
		})
	}
}

func checkPost(t *testing.T, r run.Result, rel, code string) {
	t.Helper()
	if code == "" {
		ticketkit.Lacks(t, r.Stderr, "POST_")
		if r.Code != 0 {
			t.Errorf("終了コード %d, want 0: %s", r.Code, r.Stderr)
		}
		return
	}
	if r.Code != 2 {
		t.Errorf("終了コード %d, want 2\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	ticketkit.Has(t, r.Stderr, code)
	ticketkit.Has(t, slash(r.Stderr), rel)
}

// withAllowRule はルールがワークツリーを allow で開ける、子の範囲 src/* の木。
func withAllowRule(t *testing.T) *ws {
	t.Helper()
	t.Parallel()
	w := family(t, scope{Allow: []string{"src/*"}}, scope{Allow: []string{"src/*"}})
	w.useRules(rulesFor("allow"))
	return w
}

// チケットの置き場（提案）は、チケットの範囲の外でも止めない。置き場の前置に続けただけの場所は置き場ではない。
func TestTicketPlacesAreExemptFromTheScopeBeforeAWrite(t *testing.T) {
	w := withAllowRule(t)
	for _, c := range []struct{ rel, decision, code string }{
		{tickets + "/todo/i0001-01-01.md", "allow", ""},
		{tickets + "/todo/i0009.md", "allow", ""},
		// 前置は / の境で切る。
		{tickets + "X/a.md", "deny", "DENY_TICKET_SCOPE"},
		{"docs/a.md", "deny", "DENY_TICKET_SCOPE"},
	} {
		r := w.writeHook(w.child, c.rel, hookOpts{})
		if got := decision(t, r); got != c.decision || w.code() != c.code {
			t.Errorf("%s: 判定 %q code %q, want %q %q: %s", c.rel, got, w.code(), c.decision, c.code, reason(t, r))
		}
	}
}

func TestTicketPlacesAreExemptFromTheScopeAfterAShell(t *testing.T) {
	w := withAllowRule(t)
	for n, c := range []struct{ rel, code string }{
		{tickets + "/todo/i0001-01-01.md", ""},
		{tickets + "/todo/i0009.md", ""},
		{tickets + "X/b.md", "POST_TICKET_SCOPE"},
		{"docs/b.md", "POST_TICKET_SCOPE"},
	} {
		r := w.afterShell(w.child, c.rel, fmt.Sprintf("p%d", n), hookOpts{})
		if c.code == "" {
			ticketkit.Lacks(t, r.Stderr, "POST_TICKET_SCOPE")
		} else {
			ticketkit.Has(t, r.Stderr, c.code)
			ticketkit.Has(t, slash(r.Stderr), c.rel)
		}
	}
}

func TestSubagentStopLeavesTheProposalsAlone(t *testing.T) {
	w := withAllowRule(t)
	// 自分の提案も、他のチケットの提案も、置き場の中なら外す。
	for _, name := range []string{"i0001-01-01", "i0009"} {
		ticketkit.Write(t, filepath.Join(w.child, filepath.FromSlash(tickets), "todo", name+".md"), "x\n")
	}
	r := w.subagentStop(w.child, "sub-1")
	if r.Code != 0 {
		t.Errorf("終了コード %d: %s%s", r.Code, r.Stdout, r.Stderr)
	}
	ticketkit.Lacks(t, r.Stderr, "POST_TICKET_SCOPE")
}

func TestSubagentStopBouncesALookalikeOfTheTicketPlaces(t *testing.T) {
	w := withAllowRule(t)
	rel := tickets + "X/a.md"
	ticketkit.Write(t, filepath.Join(w.child, filepath.FromSlash(rel)), "x\n")
	r := w.subagentStop(w.child, "sub-2")
	if r.Code != 2 {
		t.Errorf("終了コード %d, want 2\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	ticketkit.Has(t, r.Stderr, "POST_TICKET_SCOPE")
	ticketkit.Has(t, slash(r.Stderr), rel)
}

// 状態の置き場・承認済みチケットは、チケットの範囲でなく組み込みの守りが止める。
func TestStateAndApprovedPlacesStayWithTheBuiltinGuard(t *testing.T) {
	w := withAllowRule(t)
	for _, c := range []struct{ name, rel, core, builtin string }{
		{"state", tickets + "/review/i0009.md", "", "builtin:ticket-state"},
		{"approved", approved + "/doing/i0009.md", "enable", "builtin:guard-project-home"},
	} {
		t.Run(c.name, func(t *testing.T) {
			r := w.writeHook(w.child, c.rel, hookOpts{core: c.core})
			if decision(t, r) != "deny" {
				t.Errorf("判定 = %q, want deny: %s", decision(t, r), reason(t, r))
			}
			ticketkit.Has(t, reason(t, r), c.builtin)
			ticketkit.Lacks(t, reason(t, r), "DENY_TICKET_SCOPE")
		})
	}
}

// 範囲外は報告するが戻さない。戻す根拠はルールの deny だけ（post._restorable）。
// 報告しているのはルールファイルに無いルール（builtin:ticket-scope）で、CCNAVI_RESTORE_IF_DENY が言う「deny と宣言した場所」ではない。
func TestPostToolUseReportsAScopeFindingWithoutRestoringIt(t *testing.T) {
	w := withAllowRule(t)
	rel := "docs/b.md"
	r := w.afterShell(w.child, rel, "keep", hookOpts{restore: "enable"})
	ticketkit.Has(t, r.Stderr, "POST_TICKET_SCOPE")
	ticketkit.Has(t, slash(r.Stderr), rel)
	ticketkit.Lacks(t, r.Stderr, "restored: ccnavi")
	ticketkit.Lacks(t, r.Stderr, "moved this file to")
	if !ticketkit.Exists(filepath.Join(w.child, filepath.FromSlash(rel))) {
		t.Error("範囲外のファイルを動かした")
	}
	// 手順だけだと「自分で消せ」としか読めない。戻さなかった理由をつける。
	ticketkit.Has(t, r.Stderr, "not-restored:")
}

// 下書きの置き場（wip/handoff/）は、追跡される置き場なので、実行前・実行後・サブエージェント終了時のどれでも範囲を適用しない。
// 外すのは wip/handoff/ の下だけで、wip/ のほかの場所や紛らわしい名前は範囲の外として止まる。
// ワークツリー直下の scratchpad/（廃止した置き場）も特別扱いしない。大文字小文字は、どの機械でも区別しない。
func TestHandoffPlaceIsExemptFromTheScopeOnlyBelowIt(t *testing.T) {
	w := withAllowRule(t)
	cases := []struct {
		rel    string
		exempt bool
		why    string
	}{
		{"wip/handoff/draft.yml", true, "下書きの置き場そのもの"},
		{"wip/handoff/staging/rules.yml", true, "その下も置き場"},
		{"wip/handoffX/a.md", false, "前置に続けただけの場所は置き場ではない"},
		{"wip/design/a.md", false, "wip/ のほかの場所には広げない"},
		{"docs/wip/handoff/a.md", false, "ルートの直下の wip/ だけ"},
		{"scratchpad/a.md", false, "ワークツリー直下の廃止した置き場。普通の作業対象"},
		{"WIP/scratchpad/a.md", false, "wip/ の下の scratchpad は置き場ではない"},
		{"WIP/Handoff/a.md", true, "表記は区別しない"},
	}
	t.Run("pre", func(t *testing.T) {
		for _, c := range cases {
			r := w.writeHook(w.child, c.rel, hookOpts{})
			want, code := "deny", "DENY_TICKET_SCOPE"
			if c.exempt {
				want, code = "allow", ""
			}
			if got := decision(t, r); got != want || w.code() != code {
				t.Errorf("%s（%s）: 判定 %q code %q, want %q %q: %s", c.rel, c.why, got, w.code(), want, code, reason(t, r))
			}
		}
	})
	// ディレクトリとファイルの両方は 1 つのツリーに置けないので、素の wip/handoff は別に見る。
	t.Run("a-file-named-like-the-place", func(t *testing.T) {
		r := w.writeHook(w.child, "wip/handoff", hookOpts{})
		if decision(t, r) != "deny" || w.code() != "DENY_TICKET_SCOPE" {
			t.Errorf("判定 %q code %q: %s", decision(t, r), w.code(), reason(t, r))
		}
	})
	t.Run("post", func(t *testing.T) {
		exempt := w.afterShell(w.child, "wip/handoff/draft.yml", "s-in", hookOpts{})
		ticketkit.Lacks(t, exempt.Stderr, "POST_TICKET_SCOPE")
		other := w.afterShell(w.child, "wip/handoffX/draft.yml", "s-out", hookOpts{})
		ticketkit.Has(t, other.Stderr, "POST_TICKET_SCOPE")
		ticketkit.Has(t, slash(other.Stderr), "wip/handoffX/draft.yml")
		ticketkit.Has(t, w.afterShell(w.child, "scratchpad/draft.yml", "s-old", hookOpts{}).Stderr, "POST_TICKET_SCOPE")
	})
}

func TestSubagentStopLeavesTheHandoffPlaceAloneAndBouncesTheRest(t *testing.T) {
	w := withAllowRule(t)
	ticketkit.Write(t, filepath.Join(w.child, "wip", "handoff", "draft.yml"), "x\n")
	r := w.subagentStop(w.child, "sub-3")
	if r.Code != 0 {
		t.Errorf("置き場だけのとき終了コード %d: %s%s", r.Code, r.Stdout, r.Stderr)
	}
	ticketkit.Lacks(t, r.Stderr, "POST_TICKET_SCOPE")

	for _, rel := range []string{"scratchpad/draft.yml", "wip/handoffX/draft.yml"} {
		ticketkit.Write(t, filepath.Join(w.child, filepath.FromSlash(rel)), "x\n")
	}
	r = w.subagentStop(w.child, "sub-4")
	if r.Code != 2 {
		t.Errorf("廃止した置き場と紛らわしい名前のとき終了コード %d, want 2\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	ticketkit.Has(t, r.Stderr, "POST_TICKET_SCOPE")
	ticketkit.Has(t, slash(r.Stderr), "scratchpad/draft.yml")
	ticketkit.Has(t, slash(r.Stderr), "wip/handoffX/draft.yml")
}

// ルールが allow の場所でも、親子の判定は厳しい側を採る。
func TestParentAndChildStrictestWinsUnderRuleAllow(t *testing.T) {
	t.Parallel()
	w := family(t, scope{Allow: []string{"src/a/*"}, Ask: []string{"src/b/*"}}, scope{Allow: []string{"src/a/*", "src/b/*"}})
	w.useRules(rulesFor("allow"))
	asked := w.writeHook(w.child, "src/b/x.py", hookOpts{})
	if decision(t, asked) != "ask" {
		t.Errorf("親が ask の場所: %q: %s", decision(t, asked), reason(t, asked))
	}
	ticketkit.Has(t, reason(t, asked), "TICKET_ASK")
	allowed := w.writeHook(w.child, "src/a/x.py", hookOpts{})
	if decision(t, allowed) != "allow" {
		t.Errorf("親も子も allow の場所: %q: %s", decision(t, allowed), reason(t, allowed))
	}
}

// チケットがヒットしない場面と、ルールより先に見る点検。
func TestTicketDoesNotApplyWhereItDoesNotHit(t *testing.T) {
	t.Parallel()
	w := family(t, scope{Allow: []string{"src/*"}}, scope{Allow: []string{"src/*"}})
	w.useRules(rulesFor("allow"))
	everything := map[string]any{"version": 1, "allow": []any{map[string]any{"id": "anything", "match": "Write|Edit", "glob": "*", "message": "x"}}}
	for _, c := range []struct {
		name string
		tree string
		rel  string
		env  map[string]string
	}{
		{"チケット制御を切ると、ルールだけで決まる", w.child, ticketOutside, map[string]string{"CCNAVI_TICKET_CONTROL": "disable"}},
		{"main はチケットを持たない", w.Root, "docs/x.md", nil},
	} {
		t.Run(c.name, func(t *testing.T) {
			if c.tree == w.Root {
				w.useRules(everything)
			}
			r := w.writeHook(c.tree, c.rel, hookOpts{env: c.env})
			if decision(t, r) != "allow" {
				t.Errorf("判定 = %q: %s", decision(t, r), reason(t, r))
			}
			ticketkit.Lacks(t, reason(t, r), "DENY_TICKET_SCOPE")
		})
	}
}

// 承認前の提案は何もしない。
func TestUnapprovedProposalDoesNothing(t *testing.T) {
	t.Parallel()
	w := &ws{Tree: ticketkit.New(t)}
	w.ProposeTicket("i0001", ticketkit.TicketOpts{Allow: []string{"src/*"}})
	w.useRules(rulesFor("allow"))
	r := w.writeHook(w.ParentTree, "docs/x.md", hookOpts{})
	if decision(t, r) != "allow" {
		t.Errorf("判定 = %q: %s", decision(t, r), reason(t, r))
	}
	ticketkit.Lacks(t, reason(t, r), "DENY_TICKET_SCOPE")
}

// ルールより前の点検（サブエージェントのチケット操作の拒否）は、ルールが allow でも先に効く。
func TestChecksBeforeTheRulesStillComeFirst(t *testing.T) {
	t.Parallel()
	w := family(t, scope{Allow: []string{"src/*"}}, scope{Allow: []string{"src/*"}})
	w.useRules(map[string]any{"version": 1, "allow": []any{map[string]any{"id": "shell", "match": "Bash", "regex": ".", "message": "x"}}})
	r := w.hook("PreToolUse", "Bash", w.ParentTree, map[string]any{"command": "sh .ccnavi/scripts/ccnavi-ticket.sh finish i0001-01-01"}, hookOpts{agent: "sub-1"})
	ticketkit.Has(t, reason(t, r), "DENY_SUBAGENT_TICKET_OP")
}

// ルールが allow の場所でチケットが止める判定（ゲート）は、dry-run でも止める。
func TestDryRunStillStopsWhereTheTicketGates(t *testing.T) {
	w := withAllowRule(t)
	out := parse(t, w.writeHook(w.child, ticketOutside, hookOpts{mode: "dry-run"}))
	if out.decision != "deny" {
		t.Errorf("判定 = %q", out.decision)
	}
	ticketkit.Has(t, out.reason, "DENY_TICKET_SCOPE")
	ticketkit.Has(t, out.reason, "still applies")
}

// ルールの読み込みに失敗して組み込みに戻っても、チケットの範囲は効く。
func TestBuiltinFallbackStillAppliesTheTicket(t *testing.T) {
	t.Parallel()
	w := family(t, scope{Allow: []string{"src/*"}}, defaultChild)
	w.useRulesText("version: 1\ndeny: [\n  - id: x\n")
	r := w.writeHook(w.child, ticketOutside, hookOpts{})
	if decision(t, r) != "deny" {
		t.Errorf("判定 = %q: %s", decision(t, r), reason(t, r))
	}
	ticketkit.Has(t, reason(t, r), "DENY_TICKET_SCOPE")
}

// 承認画面の注記。親の画面の「編集可能な範囲」にだけ付き、子の画面には付かない。
func TestApprovalScreenNoteIsOnTheParentOnly(t *testing.T) {
	const approvalNote = "ルールで許可（allow）されていても、このチケットの範囲外では編集できません。" +
		"ルールで禁止（deny）にした場所は、この範囲の中でも止まります"
	t.Parallel()
	w := &ws{Tree: ticketkit.New(t)}
	w.ProposeTicket("i0001", ticketkit.TicketOpts{Allow: []string{"src/*"}})
	w.ProposeTicket("i0001-01-01", ticketkit.TicketOpts{Parent: "i0001", Phase: 1, Allow: []string{"src/a/*"}})
	r := ticketkit.OK(t, w.Ccnavi("--agree", "--preview"))
	// 見出し「このチケットで編集可能な範囲」の節の中に出る。
	ticketkit.Has(t, sectionOf(r.Stdout, "■ このチケットで編集可能な範囲"), approvalNote)
	child := sectionOf(r.Stdout, "== i0001-01-01")
	if child == "" {
		t.Fatalf("子の節が無い:\n%s", r.Stdout)
	}
	ticketkit.Lacks(t, child, approvalNote)
}

// 診断（--test）はルールとチケットの両方を名指しする。
func TestTestCommandNamesBothTheRuleAndTheTicket(t *testing.T) {
	w := withAllowRule(t)
	r := ticketkit.OK(t, w.Ccnavi("--test", "Write", filepath.Join(w.child, "docs", "x.md")))
	for _, want := range []string{"verdict: deny (DENY_TICKET_SCOPE)", "builtin:ticket-scope", "rule-allow"} {
		ticketkit.Has(t, r.Stdout, want)
	}
}

// 診断（--explain）は、チケットの範囲が ルールの allow・ask より優先されると言う。
func TestExplainSaysTheTicketIsStrongerThanRuleAllow(t *testing.T) {
	const explainNote = "ワークツリーに結び付いたチケットの範囲は、ルールの allow / ask より優先されます。" +
		"編集可能範囲の外への書き込みは拒否されます。"
	t.Parallel()
	w := family(t, scope{Allow: []string{"src/*"}}, defaultChild)
	r := ticketkit.OK(t, w.Ccnavi("--explain"))
	// 「チケットの作業範囲」の節の頭。チケット制御の行と並ぶ最初の 2 行のうちに出る。
	section := sectionOf(r.Stdout, "■ チケットの作業範囲（承認済みチケット）")
	var head []string
	for _, line := range strings.Split(strings.TrimSpace(section), "\n") {
		head = append(head, strings.TrimSpace(line))
	}
	if len(head) > 2 {
		head = head[:2]
	}
	ticketkit.Has(t, strings.Join(head, "\n"), explainNote)
}
