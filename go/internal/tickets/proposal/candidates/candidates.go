package candidates

import (
	"cmp"
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/model/form"
	"ccnavi/internal/tickets/model/standing"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/phase"
)

// Candidate は承認の対象の 1 件。新規の提案か、親の改版か。リスクの点は持たない（子を閉じるときに実績で数える）。
type Candidate struct {
	Ticket     *ticket.Ticket
	Complaints []rules.Problem
	// Overflow は範囲の超過（親の範囲・定義の上限を超えた項、regex の項）。判定が切り詰めて止まるので、判定に
	// 影響しない記述の注意（Complaints の warn）とは分けて持つ。
	Overflow []rules.Problem
	// Current は改版なら、いま使われている承認済みチケット。
	Current *ticket.Ticket
	// Types はこのチケットに使う定義（親ならその提案のコピー、子なら親のコピー）。画面はこれでタイトルと
	// レビューの要否を出す。
	Types *phases.Types
	// Config は承認したときの phases.yml。コピーが同じかを確かめ、画面に「この親では使わない定義」を出す。
	Config *phases.Types
	// Notes は画面に足す 1 行ずつの注記（フィードバック計画の証跡など）。
	Notes []string
}

// IsRevision は親の改版か。
func (c *Candidate) IsRevision() bool { return c.Current != nil }

// PlansFeedback はフィードバック計画を初めて立てる改版か。
func (c *Candidate) PlansFeedback() bool {
	return c.Current != nil && !c.Current.FeedbackPlanned && c.Ticket.FeedbackPlanned
}

// Rejected は承認の対象から外した提案と、その理由。
type Rejected struct {
	Ticket   *ticket.Ticket
	Problems []rules.Problem
}

func hasError(problems []rules.Problem) bool {
	return slices.ContainsFunc(problems, func(p rules.Problem) bool { return p.Severity == rules.Error })
}

// Candidates は承認の対象に入れるものと外すものに分ける。3 つめは親子を引く対応表（承認済みと、今回通ったもの）。
//
// 外れた親を対応表に残すと、承認されない親の範囲で子が検証され、親の承認を経ずに子の承認済みチケットができる。
// 改版は通ったものだけを対応表に入れ、子はフェーズの番号の順に検める（識別子の順だと、前のフェーズに足す子より
// 後のフェーズの子が先に検査され、開き直す前のマーカーで通ってしまう）。
func Candidates(conf *config.Settings, root string, pending, revisions, approved []*ticket.Ticket) ([]*Candidate, []Rejected, map[string]*ticket.Ticket) {
	openIndex := approval.ByID(approved)
	pool := approval.ByID(approved)
	var batch []*Candidate
	var rejected []Rejected
	cache := map[string]*phases.Types{}
	cached := map[string]bool{}
	var preds approval.Pool
	fams := standing.NewFamilies(conf, root)
	mixed := feedbackWithPlanChildren(revisions, pending, openIndex)

	configFor := func(t *ticket.Ticket) *phases.Types {
		name := ProjectOf(t, pool)
		if !cached[name] {
			cache[name] = phases.LoadTypes(conf, root, name)
			cached[name] = true
		}
		return cache[name]
	}

	for _, t := range sortedBy(revisions, func(a, b *ticket.Ticket) int { return strings.Compare(a.ID, b.ID) }) {
		current := openIndex[t.ID]
		cfg := configFor(t)
		t.Workflow = phases.Compute(t)
		types := phases.TypesOf(t)
		complaints := fieldProblems(t)
		complaints = append(complaints, form.Problems(t, true)...)
		complaints = append(complaints, revisionProblems(conf, root, t, current, cfg)...)
		complaints = append(complaints, approval.FamilyProblems(conf, root, t, fams)...)
		complaints = append(complaints, approval.MRProblems(t)...)
		if kids, ok := mixed[t.ID]; ok {
			complaints = append(complaints, mixedProblem(t, kids, true))
		}
		if hasError(complaints) {
			rejected = append(rejected, Rejected{t, complaints})
			continue
		}
		cand := &Candidate{Ticket: t, Complaints: complaints, Current: current, Types: types, Config: cfg}
		if cand.PlansFeedback() {
			cand.Notes = feedbackNotes(t)
		}
		batch = append(batch, cand)
		pool[t.ID] = t
	}

	added := map[string][]*ticket.Ticket{}
	proposed := proposedPhases(conf, root, pending)
	others := slices.Concat(approved, pending, revisions)
	ordered := sortedBy(pending, func(a, b *ticket.Ticket) int {
		return cmp.Or(
			strings.Compare(cmp.Or(a.Parent, a.ID), cmp.Or(b.Parent, b.ID)),
			cmp.Compare(boolInt(a.IsChild()), boolInt(b.IsChild())),
			cmp.Compare(a.Phase, b.Phase),
			strings.Compare(a.ID, b.ID),
		)
	})
	for _, t := range ordered {
		cfg := configFor(t)
		if !t.IsChild() && t.HasPlan() {
			t.Workflow = phases.Compute(t)
		}
		types := typesFrom(t, pool)
		missing := ""
		if t.IsChild() && pool[t.Parent] == nil {
			missing = ParentElsewhereText(conf, root, t, nil)
		}
		complaints, overflow := Validate(t, pool, cfg, false, missing)
		if !t.IsChild() && t.HasPlan() {
			complaints = append(complaints, phases.ChildlessReady(t, proposed[t.ID])...)
		}
		complaints = append(complaints, fieldProblems(t)...)
		complaints = append(complaints, form.Problems(t, false)...)
		complaints = append(complaints, approval.ProjectProblems(t, pool, conf)...)
		complaints = append(complaints, approval.PlaceProblems(root, conf, t)...)
		complaints = append(complaints, approval.FamilyProblems(conf, root, t, fams)...)
		complaints = append(complaints, approval.MRProblems(t)...)
		if kids, ok := mixed[t.Parent]; ok && t.IsChild() && slices.Contains(kids, t) {
			complaints = append(complaints, mixedProblem(t, kids, false))
		}
		complaints = append(complaints, approval.BranchProblems(t, others)...)
		if t.IsChild() && !hasError(complaints) {
			if parent := pool[t.Parent]; parent != nil {
				complaints = append(complaints, phase.OrderProblems(conf, root, t, parent, added[t.Parent], nil)...)
			}
		}
		if t.IsChild() && len(t.Predecessors) > 0 {
			// 先行は done/ に在って取り消しでないことを求める。同じ承認で通る先行も、まだ todo/ に在るので満たさない。
			if preds == nil {
				preds = approval.PredecessorPool(conf, root)
			}
			complaints = append(complaints, approval.PredecessorProblems(t, preds, conf.Approved)...)
		}
		if hasError(complaints) {
			rejected = append(rejected, Rejected{t, complaints})
			continue
		}
		batch = append(batch, &Candidate{Ticket: t, Complaints: complaints, Overflow: overflow, Types: types, Config: cfg})
		pool[t.ID] = t
		if t.IsChild() {
			added[t.Parent] = append(added[t.Parent], t)
		}
	}
	return batch, rejected, pool
}

func boolInt(b bool) int {
	if b {
		return 1
	}
	return 0
}

func sortedBy(ts []*ticket.Ticket, by func(a, b *ticket.Ticket) int) []*ticket.Ticket {
	out := slices.Clone(ts)
	slices.SortStableFunc(out, by)
	return out
}

// proposedPhases は子の提案があるフェーズの番号（親ごと）。承認の対象を絞っても todo/ の子の提案を数える
// （絞った分だけで数えると、子を書いた親が子無しとして断られる）。
func proposedPhases(conf *config.Settings, root string, pending []*ticket.Ticket) map[string][]int {
	if !slices.ContainsFunc(pending, func(t *ticket.Ticket) bool { return !t.IsChild() && t.HasPlan() }) {
		return nil
	}
	drafted := approval.ReadProposals(conf, root, nil).Kept
	found := map[string][]int{}
	for _, t := range slices.Concat(pending, drafted) {
		if t.IsChild() && !slices.Contains(found[t.Parent], t.Phase) {
			found[t.Parent] = append(found[t.Parent], t.Phase)
		}
	}
	return found
}

// feedbackWithPlanChildren は親の識別子 → 同じ承認に入る、その親の全体計画の子。対象はフィードバック計画を
// 初めて足す改版だけ。子を足すとそのフェーズが開き直り、レビュー済みが前提の改版が成り立たないので両方を拒む。
func feedbackWithPlanChildren(revisions, pending []*ticket.Ticket, openIndex map[string]*ticket.Ticket) map[string][]*ticket.Ticket {
	out := map[string][]*ticket.Ticket{}
	for _, rev := range revisions {
		current := openIndex[rev.ID]
		if current == nil || current.FeedbackPlanned || !rev.FeedbackPlanned {
			continue
		}
		var kids []*ticket.Ticket
		for _, c := range pending {
			if c.IsChild() && c.Parent == rev.ID && c.Phase <= len(rev.Plan) {
				kids = append(kids, c)
			}
		}
		if len(kids) > 0 {
			out[rev.ID] = kids
		}
	}
	return out
}

func mixedProblem(t *ticket.Ticket, kids []*ticket.Ticket, revision bool) rules.Problem {
	names := make([]string, len(kids))
	for i, k := range kids {
		names[i] = k.ID
	}
	joined := strings.Join(names, ", ")
	who := "親 " + t.Parent + " のフィードバック計画の改版と同じ承認には入れない（" + joined + "）"
	if revision {
		who = "フィードバック計画の改版は、同じ承認で全体計画の子（" + joined + "）を足すあいだは承認しない"
	}
	return rules.Problem{Severity: rules.Error, Rule: t.ID, Detail: who +
		"。全体計画に足す子を先に承認し、その作業と再レビュー（レビュー済み）を済ませてから、" +
		"フィードバック計画の改版を承認する"}
}

// feedbackNotes はフィードバック計画の承認に添える証跡。「対応なし」の言い換えは画面の計画の差分が言う。
func feedbackNotes(parent *ticket.Ticket) []string {
	if len(parent.Feedback) == 0 {
		return []string{"別に追うものは issue に回したか（ccnavi-user-decide.sh）"}
	}
	return nil
}

// typesFrom はこのチケットに使う定義。親ならそのコピー、子なら親のコピー（親が引けなければ nil）。
func typesFrom(t *ticket.Ticket, pool map[string]*ticket.Ticket) *phases.Types {
	if t.IsChild() {
		if parent := pool[t.Parent]; parent != nil {
			return phases.TypesOf(parent)
		}
		return nil
	}
	return phases.TypesOf(t)
}

// ProjectOf はこのチケットのレイヤーを決める project:。子は親のプロジェクトを使う（食い違えば ProjectProblems が
// 外す）。親が対応表に居ないときだけ、子の置き場の値を読む。
func ProjectOf(t *ticket.Ticket, pool map[string]*ticket.Ticket) string {
	if t.IsChild() {
		if parent := pool[t.Parent]; parent != nil {
			return parent.Project
		}
	}
	return t.Project
}
