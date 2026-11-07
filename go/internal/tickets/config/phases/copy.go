package phases

import (
	"fmt"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/config/layer"
	"ccnavi/internal/tickets/model/ticket"
)

// 計画を持つ親は、計画（plan: / feedback:）が使う定義だけのコピーを frontmatter の phases: に持つ。承認した
// ときの定義が親に固定され、承認のあとの判定はコピーを読み、phases.yml を読まない（直しても進行中の親の範囲・
// レビューの要否・成果物は変わらない）。

// ReadCopy は親の phases: を定義に読む。読めない定義は入れずに苦情を返す（読み方と文は phases.yml と同じ。
// 苦情の rule は定義の id、マップでないときは where）。
func ReadCopy(raw any) (*Types, []rules.Problem) {
	m, ok := raw.(*yaml.Map)
	if !ok {
		return &Types{}, []rules.Problem{problem(rules.Error, where,
			"`phases:` は定義の id をキーにしたマップで書く（欄は phases.yml の定義と同じ）")}
	}
	return readDefs(m)
}

// TypesOf はチケットのコピーの定義。コピーが無ければ空。読んだものは t.PhaseTypes に持たせて使い回す。
// 出どころのレイヤーは親の project: が指すレイヤーで、project は読んだあとに置き場から入るので引くたびに付け直す。
func TypesOf(t *ticket.Ticket) *Types {
	types, ok := t.PhaseTypes.(*Types)
	if !ok {
		types = &Types{}
		if t.PhasesRaw != nil {
			types, _ = ReadCopy(t.PhasesRaw)
		}
		t.PhaseTypes = types
	}
	types.markSource(layer.Name(t.Project))
	return types
}

// HasCopy は phases: を書いているか（空の値は書いていないとみなす）。
func HasCopy(t *ticket.Ticket) bool { return t.PhasesRaw != nil }

// Origin はコピーの定義の出どころのレイヤー（記録の source）。共通レイヤーに同じ id で読んだ形も同じ定義が
// あれば共通レイヤーから来たと読む。承認のあとに共通の定義が直されていれば、親のレイヤーのまま。
func Origin(p *PhaseType, common *Types) string {
	if found := common.Get(p.ID); found != nil && Same(found, p) {
		return config.LayerCommon
	}
	return p.Source
}

// CopyProblems はチケットだけを読んで済む、コピーの誤り。判定・承認・--lint が同じ答えを引く。phases.yml は
// 読まない（承認のあとに直しても進行中の親を止めない）。子の phases:・コピーの形・計画が使う定義の欠け・kind の
// 食い違いは error、どのステップも使わない定義は warn。
func CopyProblems(t *ticket.Ticket) []rules.Problem {
	var found []rules.Problem
	name := t.ID
	errorf := func(text string) { found = append(found, problem(rules.Error, name, text)) }
	if t.IsChild() {
		if t.Raw.Has(ticket.PhasesKey) {
			errorf("`phases:` は親だけの欄（計画が使うフェーズ定義のコピー）。子は `phase:`（番号）で" +
				"親の計画のステップを指す。子の `phases:` は削除してください")
		}
		return found
	}
	fill := "`" + config.Launcher + " --plan-order " + name + " --fill-phases`"
	if HasCopy(t) {
		_, problems := ReadCopy(t.PhasesRaw)
		for _, p := range problems {
			detail := p.Detail
			if p.Rule != where {
				detail = "`phases:` の `" + p.Rule + "`: " + p.Detail
			}
			found = append(found, problem(p.Severity, name, detail))
		}
	}
	types := TypesOf(t)
	if t.HasPlan() && !HasCopy(t) {
		errorf("計画（plan / feedback）があるのに、計画が使うフェーズ定義のコピー `phases:` がありません。" +
			"提案なら " + fill + " で差し込む。承認済みチケットなら、ユーザが `phases:` を追加するか、" +
			"取り消して出し直す")
		return found
	}
	named := map[string]bool{}
	if m, ok := t.PhasesRaw.(*yaml.Map); ok {
		for k := range m.All() {
			named[ospath.Strip(pyfmt.Str(k))] = true
		}
	}
	used := map[string]bool{}
	for _, part := range []struct {
		key   string
		items []ticket.PlanItem
		kind  Kind
	}{{"plan", t.Plan, Work}, {"feedback", t.Feedback, Feedback}} {
		for i, item := range part.items {
			used[item.Type] = true
			if part.kind == Feedback && item.Review == ticket.Unneeded {
				// 定義の feedback に review: false を書けないのと同じ。計画のステップでも外せない。
				errorf(fmt.Sprintf("`%s[%d]` に `review: false` は書けない"+
					"（フィードバック対応の結果をユーザが見ない経路は作らない）", part.key, i))
			}
			p := types.Get(item.Type)
			if p == nil {
				if !named[item.Type] {
					errorf(fmt.Sprintf("`%s[%d]` の定義 `%s` が `phases:` にありません"+
						"（計画が使う定義は `phases:` にコピーして書く。%s で差し込める）", part.key, i, item.Type, fill))
				}
				continue
			}
			if p.Kind != part.kind {
				where := "フィードバック計画"
				if part.key == "plan" {
					where = "全体計画"
				}
				errorf(fmt.Sprintf("`%s[%d]` の `%s` は kind `%s`。%sには置けない", part.key, i, item.Type, p.Kind, where))
			}
		}
	}
	for p := range types.All() {
		if !used[p.ID] {
			found = append(found, problem(rules.Warn, name,
				"`phases:` の `"+p.ID+"` はどのステップも使っていない（消し忘れなら "+fill+" で外す）"))
		}
	}
	return found
}

// probe は範囲の 1 項を定義の範囲に当てるときに試すパス。ワイルドカードの前置の下の 1 つ。
func probe(e *ticket.Entry) string {
	if prefix := e.Prefix(); e.Glob != prefix {
		return prefix + "x"
	}
	return e.Glob
}

// ScopeProblems は子の範囲が定義の上限を超えている項を warn で名指しする（子 ⊆ 定義）。判定も定義の上限で
// 切り詰める。
func ScopeProblems(child *ticket.Ticket, p *PhaseType) []rules.Problem {
	if p.Inherit {
		return nil
	}
	var problems []rules.Problem
	for i := range child.Entries {
		e := &child.Entries[i]
		if e.Decision == rules.Deny {
			continue
		}
		if e.Regex != "" {
			problems = append(problems, problem(rules.Warn, child.ID, ticket.RegexOverflowDetail(e.Regex)))
			continue
		}
		if p.Decide(probe(e)) != rules.Allow {
			problems = append(problems, problem(rules.Warn, child.ID, "`"+e.Glob+"` は定義 "+p.Title+" / "+p.ID+
				" の範囲 "+strings.Join(p.ScopeGlobs, ", ")+" を超えている"))
		}
	}
	return problems
}

func normPrefix(e *ticket.Entry) string {
	return pyfmt.Lower(strings.ReplaceAll(e.Prefix(), `\`, "/"))
}

// overlaps は親の allow の 1 項と定義の範囲が重なり得るか。言うのは明らかに無関係なときだけ（偽陽性を出す
// より黙る）。字義どおりの前置のどちらかがもう片方の前置になっていれば重なり得るとみなす。
func overlaps(e *ticket.Entry, p *PhaseType) bool {
	if p.Decide(probe(e)) == rules.Allow {
		return true
	}
	mine := normPrefix(e)
	for i := range p.Scope {
		theirs := normPrefix(&p.Scope[i])
		if strings.HasPrefix(mine, theirs) || strings.HasPrefix(theirs, mine) {
			return true
		}
	}
	return false
}

// ParentScopeProblems は親の allow（ask を含む）のうち、計画のどの定義とも重ならない項を warn で名指しする。
// 承認しても子が書けないので、親を作るときと承認の確認で言う。見るのは plan のステップの定義だけ（feedback の
// 定義は多くが inherit で、混ぜると警告が効かなくなる）。inherit の定義があれば上限が無いので何も言わない。
func ParentScopeProblems(parent *ticket.Ticket) []rules.Problem {
	defs := TypesOf(parent)
	var types []*PhaseType
	for _, item := range parent.Plan {
		if p := defs.Get(item.Type); p != nil {
			types = append(types, p)
		}
	}
	if len(types) == 0 {
		return nil
	}
	for _, p := range types {
		if p.Inherit {
			return nil
		}
	}
	var problems []rules.Problem
	for i := range parent.Entries {
		e := &parent.Entries[i]
		if e.Decision == rules.Deny || e.Regex != "" {
			continue
		}
		hit := false
		for _, p := range types {
			if overlaps(e, p) {
				hit = true
				break
			}
		}
		if hit {
			continue
		}
		titles := make([]string, len(types))
		for i, p := range types {
			titles[i] = p.Title + " / " + p.ID
		}
		problems = append(problems, problem(rules.Warn, parent.ID, "`"+e.Glob+"` は計画のどの定義（"+
			strings.Join(titles, ", ")+"）の範囲とも重ならない。"+
			"子は書けない。計画に範囲を持つ定義を足すか、`scope: inherit` の定義"+
			"（`quick-work` など。範囲は親の allow そのまま）に切り替える"))
	}
	return problems
}
