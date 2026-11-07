package phase

import (
	"fmt"
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/approval/marks"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/model/ticket"
)

// 承認の順序の苦情と、親の局面の呼び名。
const (
	brokenPlanText = "親 %s の計画が不正なので、順序を数えない（%s）"
	notClosedText  = "%d 番目の子は、%s が閉じるまで承認しない（%s）。作業が終わるまで次の計画は立てない"
	notReviewText  = "%d 番目の子は、%s のレビューが済むまで承認しない"
	reopenedText   = "同じ承認で %s を追加するので再び開く"
	stillOpenText  = "同じ承認で %s を追加するが、まだ閉じていない"
	noChildText    = "子がまだ無い"
	openChildText  = "子が開いている"

	// StageFeedbackWaiting は全体計画が全部閉じてレビューも済み、フィードバック計画がまだ無い局面。親を閉じる
	// 前に改版で出す。Stop の促しとボードがこの名前で見分ける。
	StageFeedbackWaiting = "フィードバック計画待ち"
	stageCloseEarly      = "閉じられる（ユーザが早めにクローズした）"
	stageWorking         = "作業中"
	stageFeedback        = "フィードバック対応中"
	stageClosable        = "クローズ可"
	stageClosableFed     = "クローズ可（フィードバック対応済み）"
)

// OrderProblems は、N 番目の子を承認してよいかを返す。承認してよいのは、先行のフェーズが閉じてレビューが済んでいるときである。苦情は KindNotYet にする。承認は通さないが、書いた側が直すものは無い。前のフェーズが閉じれば、同じ提案が通るからである。親の計画が壊れていれば、壊れた先行で数えずに落とす。
//
// adding は、同じ承認で先に通った、同じ親の子である。承認されればそのフェーズは開き、マーカーも消える。ディスクの上では閉じていても、開いたフェーズとして読む。読まないと、1 本ずつなら外れる組み合わせが、まとめて承認すると通ってしまう。raw は Phases と同じである。
func OrderProblems(conf *config.Settings, root string, child, parent *ticket.Ticket, adding []*ticket.Ticket, raw *approval.Raw) []rules.Problem {
	number, isChild := child.ChildPhase()
	if !parent.HasPlan() || !isChild || parent.ItemAt(number) == nil {
		return nil
	}
	if broken := phases.Errors(parent); len(broken) > 0 {
		return []rules.Problem{{Severity: rules.Error, Rule: child.ID,
			Detail: fmt.Sprintf(brokenPlanText, parent.ID, broken[0].Detail)}}
	}
	waits := slices.Clone(phases.WaitsOf(parent, number))
	slices.Sort(waits)
	waits = slices.Compact(waits)
	reopened := map[int][]string{}
	for _, t := range adding {
		if n, ok := t.ChildPhase(); ok {
			reopened[n] = append(reopened[n], t.ID)
		}
	}
	// 同じ承認で計画を変える改版を通すと、改版で足した番号は並ばない。並ばない番号は子がまだ無いフェーズとして
	// 数える（見落とすと、先行すべきステップの子と一緒に通る）。
	known := phasesOf(conf, root, parent.ID, parent, raw)
	types := phases.TypesOf(parent)
	notYet := func(detail string) []rules.Problem {
		return []rules.Problem{{Severity: rules.Error, Rule: child.ID, Detail: detail, Kind: rules.KindNotYet}}
	}
	for _, m := range waits {
		if m >= number {
			break
		}
		p := find(known, m)
		if p == nil {
			p = newPhase(parent.ID, m)
			p.Item, p.owner = parent.ItemAt(m), parent
			if p.Item != nil {
				p.Type = types.Get(p.Item.Type)
			}
		}
		names, again := reopened[m]
		if !p.Ended() || again {
			var state string
			switch {
			case again && p.Ended():
				state = fmt.Sprintf(reopenedText, strings.Join(names, ", "))
			case again:
				state = fmt.Sprintf(stillOpenText, strings.Join(names, ", "))
			case len(p.Tickets) == 0:
				state = noChildText
			default:
				state = openChildText
			}
			return notYet(fmt.Sprintf(notClosedText, number, p.Label(), state))
		}
		if !p.ReviewedOrSkipped() {
			return notYet(fmt.Sprintf(notReviewText, number, p.Label()))
		}
	}
	return nil
}

// PlanFinished は全体計画のフェーズが全部閉じ、最後までレビューが済んでいるか（フィードバック計画を初めて足す
// 改版を承認できる条件）。依頼しただけでは足りない。ユーザの応えが決まる前にレビューの状態を動かさないため。
func PlanFinished(conf *config.Settings, root string, parent *ticket.Ticket) bool {
	if !parent.HasPlan() {
		return false
	}
	last := len(parent.Plan)
	seen := map[int]bool{}
	for _, p := range Phases(conf, root, parent.ID, nil) {
		if p.Number > last {
			break
		}
		if !p.Ended() || !p.ReviewedOrSkipped() {
			return false
		}
		seen[p.Number] = true
	}
	for n := 1; n <= last; n++ {
		if !seen[n] {
			return false
		}
	}
	return len(seen) == last
}

// Stage は親がいまどの局面にいるか。計画が無ければ空。raw は Phases と同じ。止まっているフェーズを呼び名ごとに
// 並べ、無ければ早めに閉じた印・作業中・フィードバック対応中・フィードバック計画待ち・クローズ可の順に見る。
func Stage(conf *config.Settings, root string, parent *ticket.Ticket, raw *approval.Raw) string {
	if !parent.HasPlan() {
		return ""
	}
	list := Phases(conf, root, parent.ID, raw)
	// 並行した枝が同時に止まっていることがある。呼び名ごとに番号を並べる。
	var labels []string
	groups := map[string][]string{}
	for _, p := range list {
		if !p.GateClosed() {
			continue
		}
		label := p.ReviewLabel()
		if _, ok := groups[label]; !ok {
			labels = append(labels, label)
		}
		groups[label] = append(groups[label], p.Label())
	}
	if len(labels) > 0 {
		parts := make([]string, len(labels))
		for i, label := range labels {
			parts[i] = label + "（" + strings.Join(groups[label], "、") + "）"
		}
		return strings.Join(parts, "、")
	}
	home := approval.HomeDirRead(conf, root, parent.ID, "", parent.Project)
	if _, ok := marks.ReadFilledParent(home, parent.ID, marks.CloseEarly); ok {
		// 残りは別の issue に書き出してあるので、閉じられる。
		return stageCloseEarly
	}
	last := len(parent.Plan)
	var open []*Phase
	for _, p := range list {
		if !p.Ended() {
			open = append(open, p)
		}
	}
	if len(open) > 0 && open[0].Number <= last {
		// 子がまだ無く、先行が済んでいないフェーズは作業中に数えない。
		var working []string
		for _, p := range open {
			if p.Number <= last && (len(p.Tickets) > 0 || waitsDone(parent, list, p.Number)) {
				working = append(working, p.Label())
			}
		}
		if len(working) == 0 {
			working = []string{open[0].Label()}
		}
		return stageWorking + "（" + strings.Join(working, "、") + "）"
	}
	if len(open) > 0 {
		where := stageWorking
		if open[0].Number > last {
			where = stageFeedback
		}
		return where + "（" + open[0].Label() + "）"
	}
	switch {
	case !parent.FeedbackPlanned:
		return StageFeedbackWaiting
	case len(parent.Feedback) > 0:
		return stageClosableFed
	}
	return stageClosable
}
