package candidates

import (
	"fmt"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/model/ticket"
)

// Validate は承認の対象にしてよいかを見る（親子の制約はここでしか見られない）。1 つめはチケットの形の苦情で、
// error があれば承認しない。2 つめは範囲の超過で、判定が親と定義の上限で切り詰める。
//
// 形の検査は判定の側も当てる（BlockingProblems）が、承認はユーザがまとめて見て決める場所なので、そこで外れる
// ものはそこで言う。config は承認したときの phases.yml、approved は承認済みチケットに当てる --lint で、
// コピーと phases.yml の食い違いを warn にする。
func Validate(t *ticket.Ticket, pool map[string]*ticket.Ticket, cfg *phases.Types, approved bool, missing string) ([]rules.Problem, []rules.Problem) {
	var problems, overflow []rules.Problem
	if t.HasMaintenance && !approved {
		// 保守用の子の印は ticket consolidate が承認済みの置き場へ直に作る。印がある提案は承認しない。
		return refuse(t, "`"+ticket.MaintenanceKey+":` は通常の提案・承認の経路では指定できない印。"+
			"提案から欄を消す（統合の子は `ccnavi-ticket.sh consolidate <親>` が作る）"), nil
	}
	if !t.IsChild() {
		if mark := t.MaintenanceProblem(); mark != "" {
			return refuse(t, mark), nil
		}
		// 承認済みの親はコピーが固定で、言われても直せない（改版が要る）。承認の前に言えば足りる。
		if t.HasPlan() && !approved {
			overflow = append(overflow, phases.ParentScopeProblems(t)...)
		}
		return PlanProblems(t, cfg, nil, approved), overflow
	}
	problems = append(problems, phases.CopyProblems(t)...)
	parent := pool[t.Parent]
	problems = append(problems, approval.ChildProblems(t, parent, missing)...)
	if parent == nil || parent.IsChild() {
		return problems, overflow
	}
	if t.IsConsolidate() {
		// 統合の子の範囲の上限は ccnavi の中に固定した定義（親の範囲は当てない）。
		if len(problems) == 0 {
			overflow = append(overflow, phases.ScopeProblems(t, phases.ConsolidateType())...)
		}
		return problems, overflow
	}
	overflow = append(overflow, ticket.SubsetProblems(t, parent)...)
	if len(problems) > 0 {
		// 番号が親の計画に無い。定義を引けないので、ここから先は見ても意味が無い。
		return problems, overflow
	}
	if parent.HasPlan() {
		if item := parent.ItemAt(t.Phase); item != nil {
			pt := phases.TypesOf(parent).Get(item.Type)
			if pt == nil {
				problems = append(problems, rules.Problem{Severity: rules.Error, Rule: t.ID, Detail: fmt.Sprintf(
					"%d 番目の定義 `%s` が親 %s の `phases:` にありません。", t.Phase, item.Type, parent.ID)})
				return problems, overflow
			}
			overflow = append(overflow, phases.ScopeProblems(t, pt)...)
		}
	}
	// regex の項は親の検査と定義の検査が同じ文で言う。画面に 2 度並べない。
	seen := map[string]bool{}
	var distinct []rules.Problem
	for _, p := range overflow {
		if !seen[p.Detail] {
			seen[p.Detail] = true
			distinct = append(distinct, p)
		}
	}
	return problems, distinct
}

// PlanProblems は親の計画とコピー（phases:）が組めるか。コピーだけで決まるものは CopyProblems、順序は
// phases.PlanProblems が当て、判定の側も同じものを当てる。延期できる定義か（レビュー不要の定義は延期するものが
// 無い）はここで見る。held は改版で、子が承認された番号が使う定義（承認済みチケットのコピーの値。nil は改版で
// ない）。approved は承認済みチケットに当てる --lint で、phases.yml との食い違いを warn にする。
func PlanProblems(t *ticket.Ticket, cfg *phases.Types, held map[string]*phases.PhaseType, approved bool) []rules.Problem {
	problems := phases.CopyProblems(t)
	if !t.HasPlan() {
		if !t.IsChild() && !approved && held == nil {
			// 改版の親は着手済みなので言わない。止まるのは ticket start で、承認依頼の前に気づけるよう先に言う。
			problems = append(problems, rules.Problem{Severity: rules.Warn, Rule: t.ID, Detail: "親の提案に計画（`plan:`）がありません。計画の無い親は承認しても" +
				"`ticket start` が止まります。" +
				"`" + config.Launcher + " ticket new <識別子> --title <タイトル> --plan <型,…>" +
				" --issue <値> --mr <番号>` の雛形に揃えてください"})
		}
		return problems
	}
	types := phases.TypesOf(t)
	for _, part := range []struct {
		key   string
		items []ticket.PlanItem
	}{{"plan", t.Plan}, {"feedback", t.Feedback}} {
		for i, item := range part.items {
			if pt := types.Get(item.Type); pt != nil && item.Deferred && !pt.Review {
				problems = append(problems, rules.Problem{Severity: rules.Error, Rule: t.ID, Detail: fmt.Sprintf(
					"`%s[%d]` の `%s` はレビュー不要の定義。延期するものがありません。", part.key, i, item.Type)})
			}
		}
	}
	problems = append(problems, phases.PlanProblems(t, types, "")...)
	return append(problems, copyConfigProblems(t, cfg, held, approved)...)
}

// copyConfigProblems はコピーの各定義が、承認したときの phases.yml の同じ名前の定義と読んだ形で同じか。
// phases.yml にあってコピーに無い定義は何も言わない（この親では使わないだけ）。held の定義は承認済みチケットの
// コピーの値と比べる（進んでいるフェーズの範囲・レビューの要否・成果物を改版で変えないため）。approved なら
// 承認済みチケットの食い違いとして warn で言う（判定はコピーを読むので、進行中の親には効かない）。
func copyConfigProblems(t *ticket.Ticket, cfg *phases.Types, held map[string]*phases.PhaseType, approved bool) []rules.Problem {
	if !phases.HasCopy(t) || !t.HasPlan() {
		return nil
	}
	// 承認前は error、承認済みの親の食い違いは warn（判定はコピーを読むので、進行中の親には効かない）。
	severity := rules.Error
	if approved {
		severity = rules.Warn
	}
	say := func(detail string) rules.Problem {
		return rules.Problem{Severity: severity, Rule: t.ID, Detail: detail}
	}
	fill := "`" + config.Launcher + " --plan-order " + t.ID + " --fill-phases`"
	if cfg == nil {
		return []rules.Problem{say("`plan` があるのにフェーズ定義（phases.yml）が読めない。" +
			"コピー（`phases:`）が phases.yml と同じかを確かめられない")}
	}
	// 定義ごとに、held（子が承認された番号が使う定義）→ 承認済みの親の phases.yml → 承認前の親の phases.yml の順に比べる。
	var problems []rules.Problem
	for pt := range phases.TypesOf(t).All() {
		ident := pt.ID
		if h, ok := held[ident]; ok {
			if !phases.Same(pt, h) {
				problems = append(problems, say("`phases:` の `"+ident+"` は子が承認された番号が使う定義なので、承認済み"+
					"チケットの値から変えられない（違う欄: "+strings.Join(phases.Differing(pt, h), ", ")+"）。"+fill+" で差し込み直す。"+
					"直したいときは、ユーザが承認済みチケットの `phases:` を直す"))
			}
			continue
		}
		ref := cfg.Get(ident)
		if approved {
			if ref == nil || !phases.Same(pt, ref) {
				what := "今の phases.yml にありません。"
				if ref != nil {
					what = "今の phases.yml と違う（違う欄: " + strings.Join(phases.Differing(pt, ref), ", ") + "）"
				}
				problems = append(problems, say("`phases:` の `"+ident+"` が"+what+"。進行中の親には反映されない（判定は"+
					"承認済みチケットのコピーを読む）。反映するには、子の無い番号の定義なら"+
					" "+fill+" を実行した改版を出す（計画が同じでも改版になる）。子が承認された"+
					"番号の定義なら、ユーザが承認済みチケットの `phases:` を直す"))
			}
			continue
		}
		if ref == nil {
			problems = append(problems, say("`phases:` の `"+ident+"` は phases.yml にありません。"))
		} else if !phases.Same(pt, ref) {
			problems = append(problems, say("`phases:` の `"+ident+"` が phases.yml の定義と違う"+
				"（違う欄: "+strings.Join(phases.Differing(pt, ref), ", ")+"）。"+fill+" でコピーし直す"))
		}
	}
	return problems
}
