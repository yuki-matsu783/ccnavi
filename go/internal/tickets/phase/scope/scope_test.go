package scope

import (
	"strings"
	"testing"

	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/model/ticket"
)

// 範囲の当て方の約束。Python と同じ答えになることは phase の TestMatchesPython が実際のワークスペースで見る。

func parse(t *testing.T, text string) *ticket.Ticket {
	t.Helper()
	found, problems := ticket.Parse(text)
	if found == nil {
		t.Fatalf("読めない: %v", problems)
	}
	return found
}

func allow(globs ...string) string {
	out := "allow:\n"
	for _, g := range globs {
		out += "  - match: Write|Edit\n    glob: \"" + g + "\"\n"
	}
	return out
}

const workCopy = "phases:\n  work:\n    kind: work\n    title: 作業\n    review: false\n    scope: inherit\n" +
	"  design:\n    kind: work\n    title: 設計\n    review: true\n    scope:\n    - wip/design/*\n"

func parentOf(t *testing.T, plan string, globs ...string) *ticket.Ticket {
	return parse(t, "---\nversion: 1\nticket: i0001\nmr: 1\nissue: 1\n"+workCopy+"plan:\n"+plan+
		"human_review:\n  required: true\n  reason: t\ntitle: 親\nrationale: r\n"+allow(globs...)+"---\n\n本文\n")
}

func childOf(t *testing.T, id, phase string, extra string, globs ...string) *ticket.Ticket {
	return parse(t, "---\nversion: 1\nticket: "+id+"\nparent: i0001\nphase: "+phase+"\n"+extra+
		"human_review:\n  required: false\n  reason: t\ntitle: 子\nrationale: r\n"+allow(globs...)+"---\n\n本文\n")
}

func TestOfNarrowsByChildParentAndType(t *testing.T) {
	parent := parentOf(t, "  - work\n  - design\n", "src/*", "wip/*")
	designer := childOf(t, "i0001-02-01", "2", "", "wip/design/*", "src/*", "docs/*")
	worker := childOf(t, "i0001-01-01", "1", "", "src/*", "docs/*")
	tests := []struct {
		name  string
		child *ticket.Ticket
		rel   string
		want  rules.Decision
		limit Limit
	}{
		{"3 つの上限の中", designer, "wip/design/a.md", rules.Allow, limitNone},
		{"子の範囲の外", designer, "tests/a", ticket.Outside, limitTicket},
		{"親の範囲の外は親を名指す", designer, "docs/a.md", ticket.Outside, LimitParent},
		{"定義の上限の外は定義を名指す", designer, "src/a.go", ticket.Outside, LimitType},
		{"大文字小文字の違いは同じ場所", designer, "WIP/Design/a.md", rules.Allow, limitNone},
		{"inherit の定義は切り詰めない", worker, "src/a.go", rules.Allow, limitNone},
		{"inherit でも親の範囲では切り詰める", worker, "docs/a.md", ticket.Outside, LimitParent},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			v := Of(tt.child, parent, TypeFor(tt.child, parent), tt.rel)
			if v.Decision != tt.want || v.Limit != tt.limit {
				t.Errorf("Of = %q %q", v.Decision, v.Limit)
			}
			if v.Outside() != (tt.want != rules.Allow) {
				t.Errorf("Outside = %v", v.Outside())
			}
		})
	}
}

// 信頼できない子は範囲の中でも止める（親が引けない子は、どの範囲で切り詰めるかが決まらない）。
func TestOfStopsABlockedTicketEvenInsideItsScope(t *testing.T) {
	parent := parentOf(t, "  - work\n", "src/*")
	child := childOf(t, "i0001-01-01", "1", "", "src/*")
	child.Blocked = "読めるが信じられない"
	pt := TypeFor(child, parent)
	v := Of(child, parent, pt, "src/a.go")
	if v.Decision != rules.Deny || v.Limit != LimitBlocked || !v.Outside() || v.Type != pt {
		t.Errorf("Of = %+v", v)
	}
}

func TestTypeForNeedsAPlannedNumber(t *testing.T) {
	parent := parentOf(t, "  - work\n  - design\n", "src/*")
	if got := TypeFor(childOf(t, "i0001-02-01", "2", "", "src/*"), parent); got == nil || got.ID != "design" {
		t.Errorf("番号 2 の定義 = %v", got)
	}
	if got := TypeFor(childOf(t, "i0001-05-01", "5", "", "src/*"), parent); got != nil {
		t.Errorf("計画に無い番号の定義 = %v", got)
	}
	if got := TypeFor(childOf(t, "i0001-01-01", "1", "", "src/*"), nil); got != nil {
		t.Errorf("親の無い子の定義 = %v", got)
	}
}

// 統合の子は親の範囲を当てず、固定の定義で切り詰める（Python の test_learning_consolidate の ScopeTest）。
func TestConsolidateChildIsCutByTheFixedDefinition(t *testing.T) {
	parent := parentOf(t, "  - work\n", "src/*")
	child := childOf(t, "i0001-00-01", "0", "maintenance: learning-consolidate\n", "docs/wiki/patterns/*", "src/*")
	pt := TypeFor(child, parent)
	if pt == nil || !strings.HasPrefix(pt.ScopeGlobs[0], "docs/wiki/patterns/") {
		t.Fatalf("固定の定義 = %+v", pt)
	}
	if want := phases.ConsolidateType().ScopeGlobs; pt.ScopeGlobs[0] != want[0] {
		t.Errorf("固定の定義の範囲 = %v", pt.ScopeGlobs)
	}
	for rel, outside := range map[string]bool{
		"docs/wiki/patterns/a.md": false,
		"src/x.py":                true, // 子の allow にあっても固定の定義の外
		"docs/wiki/inbox/a.md":    true, // 固定の定義の中でも子の allow に無い
	} {
		if v := Of(child, parent, pt, rel); v.Outside() != outside {
			t.Errorf("%s: %+v", rel, v)
		}
	}
	ordinary := childOf(t, "i0001-01-01", "1", "", "src/*", "docs/*")
	if v := Of(ordinary, parent, TypeFor(ordinary, parent), "docs/a.md"); !v.Outside() || v.Limit != LimitParent {
		t.Errorf("ふつうの子は親で切り詰める: %+v", v)
	}
}
