package templates

import (
	"fmt"
	"strings"

	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/flow"
	"ccnavi/internal/tickets/model/ids"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/proposal/candidates"
)

// checkID は計画を検査するために合成する親の識別子。苦情には出さない。
const checkID = "template-check"

// placeholderTitle は phases.yml に無い型に、順序の検査のために付ける仮の表示名の頭。
const placeholderTitle = "(定義なし) "

// part は計画の区分（全体計画かフィードバック計画）と、その置き場に合う定義の kind。
type part struct {
	key   phases.Part
	kind  phases.Kind
	items []Item
	first int // 提案の絶対番号の始まり
}

func (t *Template) parts() []part {
	return []part{
		{phases.Plan, phases.Work, t.Plan, 1},
		{phases.FeedbackPart, phases.Feedback, t.Feedback, len(t.Plan) + 1},
	}
}

// Check は計画とフローの検査。types はそのときのフェーズ定義（共通 + レイヤー。nil は無い）、cat はフローで
// 選べる名前（nil なら名前を確かめない）。順序の誤り・flows の添字・フローの形は error。型が定義に無い、kind が
// 置き場と合わない、延期の引き受け手にレビューが無い、は warn（定義は後から変わりうる。使うときの承認で
// 改めて error になる）。description か when に TodoMark が残っているのも warn。
func Check(t *Template, types *phases.Types, cat *flow.Catalog) []rules.Problem {
	said := &problems{rule: t.Name}
	for _, kv := range [][2]string{{keyDescription, t.Description}, {keyWhen, t.When}} {
		if strings.Contains(kv[1], TodoMark) {
			said.add(rules.Warn, "`"+kv[0]+"` に "+TodoMark+" が残っている（固有名の一般化が済んでいない下書き）。直してから取り込む")
		}
	}
	if why := HiddenProblem(strings.Join(strs(t.asData(), nil), "\n")); why != "" {
		said.error(why)
	}
	if total := len(t.Plan) + len(t.Feedback); total > ids.MaxChildNumber {
		said.error(fmt.Sprintf("計画が %d ステップある（上限 %d ステップ）。計画を分ける", total, ids.MaxChildNumber))
	}
	said.list = append(said.list, orderErrors(t)...)
	if !said.failed() {
		said.list = append(said.list, planProblems(t, types)...)
	}
	said.list = append(said.list, flowProblems(t, cat)...)
	return said.list
}

// orderErrors は after の範囲の誤り。after はその計画の中の添字で、自分より小さい 1 以上の数だけを指せる。
func orderErrors(t *Template) []rules.Problem {
	said := &problems{rule: t.Name}
	for _, p := range t.parts() {
		for i, item := range p.items {
			for _, v := range item.After {
				if v < 1 || v >= i+1 {
					said.error(fmt.Sprintf("`%s[%d]` の `after` の %d は読めない（%s の中の添字で、自分（%d）より小さい 1 以上の数だけを指せる）",
						p.key, i+1, v, p.key, i+1))
				}
			}
		}
	}
	return said.list
}

// planConfig は順序の検査に使う定義。(合成する親の phases:, 検査の定義, 定義が無い苦情)。定義に無い型は、
// 順序の検査のために仮の定義（kind は置き場に合わせる）へ置き換え、そのことは warn で別に言う。
func planConfig(t *Template, known *phases.Types) (*yaml.Map, *phases.Types, []rules.Problem) {
	said := &problems{rule: t.Name}
	copies := &yaml.Map{}
	var placeholders []*phases.PhaseType
	for _, p := range t.parts() {
		for i, item := range p.items {
			if copies.Has(item.Type) {
				continue
			}
			if real := known.Get(item.Type); real != nil {
				copies.Put(item.Type, real.AsCopy())
				continue
			}
			pt := &phases.PhaseType{ID: item.Type, Title: placeholderTitle + item.Type, Kind: p.kind,
				Review: true, Inherit: true, Learning: phases.LearningOn}
			copies.Put(item.Type, pt.AsCopy())
			placeholders = append(placeholders, pt)
			if known != nil {
				said.add(rules.Warn, fmt.Sprintf("`%s[%d]` の型 `%s` が phases.yml にありません（定義が変わったか、このレイヤーにありません。使う前に直す）",
					p.key, i+1, item.Type))
			}
		}
	}
	if known == nil && len(placeholders) > 0 {
		said.add(rules.Warn, "フェーズ定義（phases.yml）が無いので、型が在るか・kind が合うかは確かめていない")
	}
	all := known
	if len(placeholders) > 0 {
		all = known.With(placeholders...)
	}
	return copies, all, said.list
}

// composed は検査のために合成する親の提案の本文。フィードバック計画の after は全体計画の続きの番号にする。
func composed(t *Template, copies *yaml.Map) string {
	front := &yaml.Map{}
	front.Put("version", ticket.Version)
	front.Put("ticket", checkID)
	front.Put(keyPlan, items(t.Plan, 0))
	if len(t.Feedback) > 0 {
		front.Put(keyFeedback, items(t.Feedback, len(t.Plan)))
	}
	front.Put(ticket.PhasesKey, copies)
	review := &yaml.Map{}
	review.Put("required", true)
	review.Put("reason", "template")
	front.Put("human_review", review)
	front.Put("title", "template")
	front.Put("rationale", "template")
	allow := &yaml.Map{}
	allow.Put("match", "Write|Edit")
	allow.Put("glob", "src/*")
	front.Put("allow", []any{allow})
	body, _ := toYAML(front)
	return ticket.Fence + "\n" + body + ticket.Fence + "\n"
}

func planProblems(t *Template, types *phases.Types) []rules.Problem {
	copies, all, found := planConfig(t, types)
	parsed, notes := ticket.Parse(composed(t, copies))
	if parsed == nil {
		for _, p := range notes {
			found = append(found, rules.Problem{Severity: p.Severity, Rule: t.Name, Detail: p.Detail})
		}
		return found
	}
	seen := map[string]bool{}
	for _, p := range phases.PlanProblems(parsed, nil, "") {
		seen[p.Detail] = true
		found = append(found, rules.Problem{Severity: rules.Error, Rule: t.Name, Detail: p.Detail})
	}
	for _, p := range candidates.PlanProblems(parsed, all, nil, true) {
		if seen[p.Detail] {
			continue
		}
		seen[p.Detail] = true
		// 定義に左右される食い違いは warn にとどめる。
		found = append(found, rules.Problem{Severity: rules.Warn, Rule: t.Name, Detail: p.Detail})
	}
	return found
}

// flowProblems は flows の添字と、フローをフローのファイルと同じ読み手と形の検査で確かめた苦情。
func flowProblems(t *Template, cat *flow.Catalog) []rules.Problem {
	said := &problems{rule: t.Name}
	planned := len(t.Plan)
	for _, index := range t.flowIndexes() {
		if index < 1 || index > planned {
			said.error(fmt.Sprintf("`flows` の添字 %d が全体計画にない（全体計画は 1〜%d）", index, planned))
			continue
		}
		data, why := flowRead(t.Flows[index])
		if data == nil {
			said.error(fmt.Sprintf("`flows[%d]`: %s", index, why))
			continue
		}
		lines := flow.StructureProblems(data)
		if cat != nil {
			lines = append(lines, flow.NameProblems(data, *cat)...)
		}
		for _, l := range lines {
			said.add(rules.Warn, fmt.Sprintf("`flows[%d]`: %s", index, l))
		}
	}
	return said.list
}

func flowRead(body *yaml.Map) (*yaml.Map, string) {
	text, why := dumpFlow(body)
	if why != "" {
		return nil, why
	}
	return flow.Parse([]byte(text))
}

// graph は計画の図の中身。--plan-order の計画ごとの図（ApprovePlan）と同じ形で、計画ごとに 1 件。番号は全体計画が
// 1..n、フィードバック計画はその続き。after が読めないときはステップと after を Template から組み、loose と
// ready は空にする。problems には順序の誤りだけを入れる。
func graph(t *Template, types *phases.Types, sha string) []any {
	copies, all, _ := planConfig(t, types)
	parsed, _ := ticket.Parse(composed(t, copies))
	order := orderErrors(t)
	var wf *ticket.Workflow
	if parsed != nil && !hasError(order) {
		wf = phases.Compute(parsed)
	}
	var loose, ready []int
	if parsed != nil && wf != nil {
		loose, ready = phases.Loose(parsed, wf), phases.Ready(parsed, wf)
	}
	out := []any{}
	for _, p := range t.parts() {
		if len(p.items) == 0 {
			continue
		}
		last := p.first + len(p.items) - 1
		inPart := func(list []int) []any {
			got := []any{}
			for _, n := range list {
				if n >= p.first && n <= last {
					got = append(got, n)
				}
			}
			return got
		}
		after := pyjson.NewObject()
		rows := []any{}
		for i, item := range p.items {
			n := p.first + i
			if len(item.After) > 0 {
				shifted := []any{}
				for _, m := range item.After {
					shifted = append(shifted, m+p.first-1)
				}
				after.Set(fmt.Sprint(n), shifted)
			}
			pt := all.Get(item.Type)
			typeReview, title := ticket.Unsaid, item.Type
			if pt != nil {
				typeReview = ticket.NeedOf(pt.Review)
				if pt.Title != "" {
					title = pt.Title
				}
			}
			var reviewAt any
			if wf != nil {
				if at, ok := wf.ReviewAt[n]; ok {
					reviewAt = at
				}
			}
			rows = append(rows, pyjson.NewObject("number", n, "from", n, "type", item.Type, "title", title,
				"review", needJSON(ticket.ChosenReview(item.Review, typeReview)), "deferred", item.Deferred,
				"review_at", reviewAt, "locked", false))
		}
		errs := []any{}
		for _, e := range order {
			if strings.HasPrefix(e.Detail, "`"+string(p.key)+"[") {
				errs = append(errs, e.Detail)
			}
		}
		if wf != nil && parsed != nil {
			for _, e := range phases.PlanProblems(parsed, nil, p.key) {
				if e.Severity == rules.Error {
					errs = append(errs, e.String())
				}
			}
		}
		out = append(out, pyjson.NewObject("ticket", t.Name, "part", string(p.key), "source_sha", sha,
			"items", rows, "after", after, "proposed", after, "current", nil,
			"loose", inPart(loose), "ready", inPart(ready), "problems", errs))
	}
	return out
}

// needJSON はレビューの要否を JSON の true・false・null にする。
func needJSON(n ticket.Need) any {
	if !n.Said() {
		return nil
	}
	return n == ticket.Needed
}
