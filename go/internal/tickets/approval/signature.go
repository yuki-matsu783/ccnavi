package approval

import (
	"math/big"
	"reflect"
	"slices"
	"sort"

	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/model/ticket"
)

// SamePlan は 2 つのチケットの計画（part が phases.Plan なら全体計画、phases.FeedbackPart ならフィードバック
// 計画）が同じか。ステップごとの「定義・review・deferred・推移的に辿った先行」で比べる。after の書き方が違っても
// 先行が同じなら同じ（`after: [2]` と、--explain の「先行 1, 2」を写した `after: [1, 2]`）。フィードバック計画が
// 無いことと空は分ける。
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

// SamePhases は親のコピー（phases:）が同じか。定義を読んだ形で比べ、YAML の書き方・キーの順・既定値を省いた
// 書き方の違いは同じとする。読めない定義があれば読んだままの値で比べる（形の誤りを直しただけの提案も違いとして
// 拾う）。コピーが無いもの同士は同じ。
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

// PlanDiffers は提案の計画（全体計画かフィードバック計画）・コピー（phases:）・範囲が承認済みチケットと違うか
// （改版の条件）。先行関係は計画から決まるので、先行関係だけの改版は無い。コピーだけ・範囲だけの違いも改版に
// する（`--plan-order <親> --fill-phases` で今の phases.yml に揃えた提案を出せるように）。
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
		samePlanItems(t.Feedback, u.Feedback) && sameValue(t.Raw, u.Raw)
}

func samePlanItems(a, b []ticket.PlanItem) bool {
	return slices.EqualFunc(a, b, func(x, y ticket.PlanItem) bool { return x.Same(&y) })
}

// sameValue は YAML から読んだ値の Python の ==（マップはキーの順を見ず、数は値で比べる。True == 1）。
func sameValue(a, b any) bool {
	if x, ok := numberOf(a); ok {
		y, ok := numberOf(b)
		return ok && x.Cmp(y) == 0
	}
	switch x := a.(type) {
	case *yaml.Map:
		y, ok := b.(*yaml.Map)
		if !ok || x.Len() != y.Len() {
			return false
		}
		for k, v := range x.All() {
			w, found := y.Lookup(k)
			if !found || !sameValue(v, w) {
				return false
			}
		}
		return true
	case []any:
		y, ok := b.([]any)
		return ok && slices.EqualFunc(x, y, sameValue)
	}
	return reflect.DeepEqual(a, b)
}

// numberOf は真偽・整数・小数を比べられる数にする。NaN は数にしない（Python でも NaN != NaN）。
func numberOf(v any) (*big.Float, bool) {
	switch x := v.(type) {
	case bool:
		if x {
			return big.NewFloat(1), true
		}
		return big.NewFloat(0), true
	case int:
		return new(big.Float).SetInt64(int64(x)), true
	case *big.Int:
		return new(big.Float).SetInt(x), true
	case float64:
		if x != x {
			return nil, false
		}
		return big.NewFloat(x), true
	}
	return nil, false
}
