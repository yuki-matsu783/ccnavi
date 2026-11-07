package approval

import (
	"slices"
	"sort"

	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/model/ticket"
)

// SamePlan は、2 つのチケットの計画が同じかを返す。part が phases.Plan なら全体計画、phases.FeedbackPart ならフィードバック計画を比べる。ステップごとに「定義・review・deferred・推移的に辿った先行」で比べる。after の書き方が違っても、先行が同じなら同じとする。たとえば `after: [2]` と、--explain の「先行 1, 2」を写した `after: [1, 2]` は同じである。フィードバック計画が無いことと、空であることは分ける。
func SamePlan(a, b *ticket.Ticket, part phases.Part) bool {
	sa, oka := planSignature(a, part)
	sb, okb := planSignature(b, part)
	return oka == okb && slices.EqualFunc(sa, sb, func(x, y step) bool {
		return x.typ == y.typ && x.review == y.review && x.deferred == y.deferred && slices.Equal(x.waits, y.waits)
	})
}

type step struct {
	typ      string
	review   ticket.Need
	deferred bool
	waits    []int
}

// planSignature は比べる形と、その計画があるか（フィードバック計画を計画していなければ偽）。
func planSignature(t *ticket.Ticket, part phases.Part) ([]step, bool) {
	items, start := t.Plan, 1
	if part != phases.Plan {
		if !t.FeedbackPlanned {
			return nil, false
		}
		items, start = t.Feedback, len(t.Plan)+1
	}
	wf := phases.Compute(t)
	out := make([]step, 0, len(items))
	for i, item := range items {
		var waits []int
		if wf != nil {
			waits = wf.Waits[start+i]
		}
		out = append(out, step{item.Type, item.Review, item.Deferred, waits})
	}
	return out, true
}

// SamePhases は、親のコピー（phases:）が同じかを返す。定義を読んだ形で比べ、YAML の書き方・キーの順・既定値を省いた書き方の違いは同じとする。読めない定義があれば、読んだままの値で比べる。形の誤りを直しただけの提案も、違いとして拾うためである。コピーが無いもの同士は同じとする。
func SamePhases(a, b *ticket.Ticket) bool {
	ha, hb := phases.HasCopy(a), phases.HasCopy(b)
	if !ha || !hb {
		return ha == hb
	}
	ta, ra := readCopy(a)
	tb, rb := readCopy(b)
	if ra != "" || rb != "" {
		return ra == rb
	}
	if ta.Len() != tb.Len() {
		return false
	}
	for p := range ta.All() {
		q := tb.Get(p.ID)
		if q == nil || !phases.Same(p, q) {
			return false
		}
	}
	return true
}

// readCopy はコピーの定義と、読めない定義があるときの読んだままの値の repr（読めれば空）。
func readCopy(t *ticket.Ticket) (*phases.Types, string) {
	types, problems := phases.ReadCopy(t.PhasesRaw)
	for _, p := range problems {
		if p.Severity == rules.Error {
			return nil, "unread " + pyfmt.Repr(t.PhasesRaw)
		}
	}
	return types, ""
}

// SameScope はチケットの範囲（deny / ask / allow の項）が同じか。項の並び順と重複は区別しない。
func SameScope(a, b *ticket.Ticket) bool {
	return slices.Equal(scopeSignature(a), scopeSignature(b))
}

type scopeItem struct{ decision, glob, regex string }

func scopeSignature(t *ticket.Ticket) []scopeItem {
	seen := map[scopeItem]bool{}
	var out []scopeItem
	for _, e := range t.Entries {
		item := scopeItem{string(e.Decision), e.Glob, e.Regex}
		if !seen[item] {
			seen[item] = true
			out = append(out, item)
		}
	}
	sort.Slice(out, func(i, j int) bool {
		x, y := out[i], out[j]
		if x.decision != y.decision {
			return x.decision < y.decision
		}
		if x.glob != y.glob {
			return x.glob < y.glob
		}
		return x.regex < y.regex
	})
	return out
}

// PlanDiffers は、提案の計画（全体計画かフィードバック計画）・コピー（phases:）・範囲が、承認済みチケットと違うかを返す。これが改版の条件になる。先行関係は計画から決まるので、先行関係だけが変わる改版は無い。コピーだけ、または範囲だけの違いも改版にする。`--plan-order <親> --fill-phases` で、今の phases.yml に揃えた提案を出せるようにするためである。
func PlanDiffers(proposal, current *ticket.Ticket) bool {
	return !SamePhases(proposal, current) || !SameScope(proposal, current) ||
		!SamePlan(proposal, current, phases.Plan) || !SamePlan(proposal, current, phases.FeedbackPart)
}

// RevisionElsewhere は正とするツリーの外の todo/ にある提案が、計画・コピー・範囲の違う親の版か
// （場所違いの改版か、巻き戻しの元）。条件は承認の改版と同じ（作業中の承認済みチケットがある親で PlanDiffers）。
func RevisionElsewhere(t, current *ticket.Ticket) bool {
	return current != nil && !t.IsChild() && t.HasPlan() && PlanDiffers(t, current)
}

// sameTodo は同じ中身の todo/ の提案か。計画まで比べる（PlanItem の比べ方は after を見ない）。
func sameTodo(t, u *ticket.Ticket) bool {
	return samePlanItems(t.Plan, u.Plan) && t.FeedbackPlanned == u.FeedbackPlanned &&
		samePlanItems(t.Feedback, u.Feedback) && yaml.Equal(t.Raw, u.Raw)
}

func samePlanItems(a, b []ticket.PlanItem) bool {
	return slices.EqualFunc(a, b, func(x, y ticket.PlanItem) bool { return x.Same(&y) })
}
