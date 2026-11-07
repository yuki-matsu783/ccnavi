package phases

import (
	"fmt"
	"slices"
	"sort"
	"strconv"
	"strings"

	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/model/ids"
	"ccnavi/internal/tickets/model/ticket"
)

// 先行関係は親の計画のステップの after から都度計算し、ファイルにもチケットの欄にも書かない。決まりは 2 つ。
//   - ステップの先行は、after を推移的に辿った全部の番号。after を書かないステップは先行なし
//   - 延期したステップは、それが先行する延期していないステップのうち、番号のいちばん小さいものが引き受ける
// フィードバック計画のステップには全体計画の番号が全部と、フィードバック計画の中で after を辿った番号が先行する。
// 延期の引き受け手を 2 か所で違って言わないよう、計算はここ 1 か所に置く。

// Part は計画の区分。Plan（全体計画）か FeedbackPart（フィードバック計画）。空は両方。
type Part string

const (
	Plan         Part = "plan"
	FeedbackPart Part = "feedback"
)

type part struct {
	key   Part
	first int
	items []ticket.PlanItem
}

// parts は計画を (欄, 最初の番号, ステップ) に分ける。フィードバック計画は全体計画の続きの番号。
func parts(parent *ticket.Ticket, only Part) []part {
	out := []part{{Plan, 1, parent.Plan}}
	if len(parent.Feedback) > 0 {
		out = append(out, part{FeedbackPart, len(parent.Plan) + 1, parent.Feedback})
	}
	if only == "" {
		return out
	}
	var kept []part
	for _, p := range out {
		if p.key == only {
			kept = append(kept, p)
		}
	}
	return kept
}

func sortedKeys(set map[int]bool) []int {
	out := make([]int, 0, len(set))
	for n := range set {
		out = append(out, n)
	}
	sort.Ints(out)
	return out
}

// Compute は親の計画の先行関係を計算する。形の誤った after は線として数えない。
func Compute(parent *ticket.Ticket) *ticket.Workflow {
	wf := &ticket.Workflow{Order: ticket.WorkflowDAG, Waits: map[int][]int{}, ReviewAt: map[int]int{}}
	// 1. 全体計画。after の番号（自分より前のものだけ）と、その先行を推移的にたどった番号を、先行の集合にする。
	planned := len(parent.Plan)
	for i, item := range parent.Plan {
		n := i + 1
		found := map[int]bool{}
		// 前の番号の先行は推移的に閉じているので、直接の先行ごとに 1 度足せば済む。
		for _, m := range item.After {
			if 1 <= m && m < n {
				found[m] = true
				for _, w := range wf.Waits[m] {
					found[w] = true
				}
			}
		}
		wf.Waits[n] = sortedKeys(found)
	}
	// 2. フィードバック計画。全体計画の全部が終わってから始まり、その中では自分の after に従う。
	whole := make([]int, planned)
	for i := range whole {
		whole[i] = i + 1
	}
	for i, item := range parent.Feedback {
		n := planned + 1 + i
		inside := map[int]bool{}
		for _, m := range item.After {
			if planned < m && m < n {
				inside[m] = true
				for _, w := range wf.Waits[m] {
					if w > planned {
						inside[w] = true
					}
				}
			}
		}
		wf.Waits[n] = append(append([]int{}, whole...), sortedKeys(inside)...)
	}
	// 3. 延期したステップは、レビューを引き受けるステップを決める。
	steps := parent.Numbered()
	for _, s := range steps {
		if !s.Item.Deferred {
			continue
		}
		if target, ok := deferTarget(wf, steps, s.Number); ok {
			wf.ReviewAt[s.Number] = target
		}
	}
	return wf
}

// deferTarget は延期した n 番目を引き受ける番号。後ろで n が先行する、延期していない最小の番号。
// n が先行していない番号が引き受けると、延期した作業が閉じる前にそのレビューが済んでしまう。
func deferTarget(wf *ticket.Workflow, steps []ticket.Step, n int) (int, bool) {
	for _, s := range steps {
		if s.Number <= n || s.Item.Deferred {
			continue
		}
		if slices.Contains(wf.Waits[s.Number], n) {
			return s.Number, true
		}
	}
	return 0, false
}

// effective は判定に使う先行関係。入れてあればそれを、無ければその場で計算する。
func effective(parent *ticket.Ticket) *ticket.Workflow {
	if parent.Workflow != nil {
		return parent.Workflow
	}
	return Compute(parent)
}

// WaitsOf は N 番目の先行の番号。計画に無い番号は前の番号を全部先行とする（止まる側）。
func WaitsOf(parent *ticket.Ticket, number int) []int {
	if waits, ok := effective(parent).Waits[number]; ok {
		return append([]int(nil), waits...)
	}
	out := []int{}
	for m := 1; m < number; m++ {
		out = append(out, m)
	}
	return out
}

func orEffective(parent *ticket.Ticket, wf *ticket.Workflow) *ticket.Workflow {
	if wf != nil {
		return wf
	}
	return effective(parent)
}

// Loose は最後のステップの先行になっていないステップの番号（それぞれの計画で）。終端の崩れ。wf が nil なら
// 判定に使う先行関係で見る。
func Loose(parent *ticket.Ticket, wf *ticket.Workflow) []int {
	wf = orEffective(parent, wf)
	var out []int
	for _, p := range parts(parent, "") {
		if len(p.items) < 2 {
			continue
		}
		last := p.first + len(p.items) - 1
		for m := p.first; m < last; m++ {
			if !slices.Contains(wf.Waits[last], m) {
				out = append(out, m)
			}
		}
	}
	return out
}

// Ready はすぐ始まるステップの番号。その計画の中で先行の無いステップ（フィードバック計画は全体計画を除いて）。
func Ready(parent *ticket.Ticket, wf *ticket.Workflow) []int {
	wf = orEffective(parent, wf)
	var out []int
	for _, p := range parts(parent, "") {
		for n := p.first; n < p.first+len(p.items); n++ {
			waiting := false
			for _, m := range wf.Waits[n] {
				if m >= p.first {
					waiting = true
					break
				}
			}
			if !waiting {
				out = append(out, n)
			}
		}
	}
	return out
}

// ChildlessMark は ChildlessReady の警告を見分ける文言。承認の確認の「次:」はこれを見て子を書くよう言い換える。
const ChildlessMark = "子の提案がありません"

// ChildlessReady は全体計画ですぐ始まるのに、子の提案が 1 つも無いステップの警告。子を後から出すと承認が
// もう 1 回要る。止めないのは、親を先に承認して子を後から出す進め方も正規だから。先行のあるステップと
// フィードバック計画のステップは見ない。childPhases は同じ承認で通す子のフェーズ番号。
func ChildlessReady(parent *ticket.Ticket, childPhases []int) []rules.Problem {
	var found []rules.Problem
	for _, n := range Ready(parent, nil) {
		item := parent.ItemAt(n)
		if !parent.InPlan(n) || item == nil || slices.Contains(childPhases, n) {
			continue
		}
		found = append(found, problem(rules.Warn, parent.ID, fmt.Sprintf(
			"計画の %d 番目（%s）はすぐ始まるのに、%s。`%s` のような子を同じ承認で通さないと、子の承認がもう 1 回要る",
			n, item.Type, ChildlessMark, ids.ChildID(parent.ID, n, 1))))
	}
	return found
}

func joinInts(list []int) string {
	parts := make([]string, len(list))
	for i, n := range list {
		parts[i] = strconv.Itoa(n)
	}
	return strings.Join(parts, ", ")
}

// TerminalProblems は終端の崩れ（最後のステップに全部が推移的に先行しているか、最後のステップが延期されて
// いないか）。wf が nil なら計算する。送る時点の検査が AfterProblems と合わせて呼ぶ。
func TerminalProblems(parent *ticket.Ticket, wf *ticket.Workflow, only Part) []rules.Problem {
	if wf == nil {
		wf = Compute(parent)
	}
	gaps := Loose(parent, wf)
	var found []rules.Problem
	for _, p := range parts(parent, only) {
		last := p.first + len(p.items) - 1
		var cut []int
		for _, m := range gaps {
			if p.first <= m && m < last {
				cut = append(cut, m)
			}
		}
		if len(cut) > 0 {
			found = append(found, problem(rules.Error, parent.ID, fmt.Sprintf(
				"`%s` の最後のステップ（%d: %s）に %s が先行していない。終端は 1 つにしてください"+
					"（最後のステップにほかの全部が推移的に先行するように `after` を書く。"+
					"独立した作業は最後に 1 ステップで受けるか、一直線に並べる）",
				p.key, last, p.items[len(p.items)-1].Type, joinInts(cut))))
		}
		if len(p.items) > 0 && p.items[len(p.items)-1].Deferred {
			found = append(found, problem(rules.Error, parent.ID,
				"`"+string(p.key)+"` の最後のステップは延期できない。誰も見ないまま終わる"))
		}
	}
	return found
}

// PlanProblems は計画の順序が組めるか（after の形、終端、最後のステップの延期、延期の引き受け手）。only を
// 渡せばその計画の誤りだけ。types（親のコピー。TypesOf）を渡したときだけ、引き受け手にレビューがあるかも見る。
func PlanProblems(parent *ticket.Ticket, types *Types, only Part) []rules.Problem {
	var found []rules.Problem
	if !parent.HasPlan() {
		return found
	}
	// 1. after の形の誤りと、同じ番号を 2 度挙げた警告。
	selected := parts(parent, only)
	found = append(found, AfterProblems(parent, only)...)
	for _, p := range selected {
		for i, item := range p.items {
			if len(item.AfterRepeated) > 0 {
				found = append(found, problem(rules.Warn, parent.ID, fmt.Sprintf(
					"`%s[%d]` の `after` に同じ番号を 2 度挙げている（%s）。1 つにまとめて読む",
					p.key, i, joinInts(item.AfterRepeated))))
			}
		}
	}
	// 2. 終端（合流）の検査。
	wf := Compute(parent)
	found = append(found, TerminalProblems(parent, wf, only)...)
	for _, p := range selected {
		for i, item := range p.items {
			n := p.first + i
			if !item.Deferred || i == len(p.items)-1 {
				continue
			}
			// 3. 延期したステップごとに、引き受け手がいるか、引き受け手にレビューがあるかを見る。最後のステップの延期は、引き受け手がいないので別の検査で断る。
			target, ok := wf.ReviewAt[n]
			if !ok {
				found = append(found, problem(rules.Error, parent.ID, fmt.Sprintf(
					"`%s[%d]` の延期を引き受けるステップがありません"+
						"（このステップが先行する、延期していない後ろのステップが必要です）", p.key, i)))
				continue
			}
			taker := parent.ItemAt(target)
			if pt := types.Get(taker.Type); pt != nil &&
				ticket.ChosenReview(taker.Review, ticket.NeedOf(pt.Review)) != ticket.Needed {
				found = append(found, problem(rules.Error, parent.ID, fmt.Sprintf(
					"`%s[%d]` を延期した先の `%s` にレビューがありません。", p.key, i, taker.Type)))
			}
		}
	}
	return found
}

// AfterProblems はステップの after の形の誤り。only を渡せばその計画だけ。--plan-order は振り直した
// ステップを組み直すので、振り直す前のステップの誤りをここで拾って足す。
func AfterProblems(parent *ticket.Ticket, only Part) []rules.Problem {
	var found []rules.Problem
	for _, p := range parts(parent, only) {
		for i, item := range p.items {
			for _, e := range item.AfterErrors {
				found = append(found, problem(rules.Error, parent.ID, fmt.Sprintf(
					"`%s[%d]` の `after` の %s は読めない（%s）", p.key, i, pyfmt.Repr(e.Value), e.Reason)))
			}
		}
	}
	return found
}

// Errors は承認済みチケットだけを読んで済む計画と定義の誤り（判定の側が当てる分）。コピーの誤りと計画の順序の
// 誤りのうち error だけ。引き受け手のレビューはコピーの定義で見る。
func Errors(parent *ticket.Ticket) []rules.Problem {
	var out []rules.Problem
	for _, p := range append(CopyProblems(parent), PlanProblems(parent, TypesOf(parent), "")...) {
		if p.Severity == rules.Error {
			out = append(out, p)
		}
	}
	return out
}

// Lines はユーザ向けの先行の一覧（承認画面と --explain）。全部の番号と、すぐ始まるステップ、最後のステップの
// 先行になっていないステップ。
func Lines(parent *ticket.Ticket, wf *ticket.Workflow) []string {
	var out []string
	for _, s := range parent.Numbered() {
		text := "先行なし"
		if waits := wf.Waits[s.Number]; len(waits) > 0 {
			text = "先行 " + joinInts(waits)
		}
		tail := ""
		if at, ok := wf.ReviewAt[s.Number]; ok {
			tail = fmt.Sprintf("（レビューは %d と一緒に）", at)
		}
		out = append(out, fmt.Sprintf("%d: %s — %s%s", s.Number, s.Item.Type, text, tail))
	}
	starting := Ready(parent, wf)
	var inPlan, inFeedback []int
	for _, n := range starting {
		if parent.InPlan(n) {
			inPlan = append(inPlan, n)
		} else {
			inFeedback = append(inFeedback, n)
		}
	}
	for _, g := range []struct {
		label string
		ns    []int
	}{{"", inPlan}, {"（フィードバック計画）", inFeedback}} {
		if len(g.ns) >= 2 {
			out = append(out, "すぐ始まる"+g.label+": "+joinInts(g.ns))
		}
	}
	if gaps := Loose(parent, wf); len(gaps) > 0 {
		out = append(out, "最後のステップの先行になっていない: "+joinInts(gaps))
	}
	return out
}
