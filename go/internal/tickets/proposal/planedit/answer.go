package planedit

import (
	"cmp"
	"fmt"
	"slices"
	"strconv"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/flow"
	"ccnavi/internal/tickets/model/ids"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/proposal/candidates"
)

// Answer は振り直した答え。`--plan-order --json` と承認の preview の plans が同じものから作る。
type Answer struct {
	source     *Source
	renumbered renumbered
	revised    *ticket.Ticket
	problems   []rules.Problem
	// revisionProblems は改版の錠に当たったもの。
	revisionProblems []rules.Problem
	// changes は付け替える子の提案（番号の変わるステップを指す子と、その子を predecessors で指す子）。
	changes []childChange
	// childProblems は付け替えられない理由（付け替え先の衝突、承認済みの側の参照）。
	childProblems []string
	// redrawn は --order で線を引き直した計画。
	redrawn []phases.Part
}

// writable は書き戻してよいか。
func (a *Answer) writable() bool {
	return len(a.renumbered.refused) == 0 && len(a.problems) == 0 && len(a.revisionProblems) == 0 && len(a.childProblems) == 0
}

// childChange は子の提案 1 本の付け替え。識別子・phase:・predecessors の新しい値。
type childChange struct {
	ticket          *ticket.Ticket
	raw             []byte
	newTicket       string
	newPhase        any // 子でなければ nil
	newPredecessors []string
}

// renamed は識別子が変わるか。
func (c *childChange) renamed() bool { return c.newTicket != c.ticket.ID }

// newPath は付け替えたあとのパス。
func (c *childChange) newPath() string {
	if !c.renamed() {
		return c.ticket.Path
	}
	return ospath.Join(ospath.Dirname(c.ticket.Path), c.newTicket+config.MdExt)
}

func (c *childChange) json() *jsonObject {
	return obj("ticket", c.ticket.ID, "new_ticket", c.newTicket, "path", c.ticket.Path, "new_path", c.newPath(),
		"phase", phaseOf(c.ticket), "new_phase", c.newPhase,
		"predecessors", strs(c.ticket.Predecessors), "new_predecessors", strs(c.newPredecessors))
}

// phaseOf はチケットのフェーズ。子でなければ nil（JSON の null）。
func phaseOf(t *ticket.Ticket) any {
	if !t.IsChild() {
		return nil
	}
	return t.Phase
}

type row struct {
	ticket       string
	phase        any
	predecessors []string
}

type retargeted struct {
	ticket, newTicket string
	newPhase          any
	newPredecessors   []string
}

// retarget は子の付け替えの決まり。この親の子（`<親>-<2 桁>-<2 桁>`）で phase が番号の変わるステップを指すものは、
// 識別子の 2 桁と phase を新しい番号にする（連番は変えない）。どの子も predecessors が付け替えた識別子を指して
// いれば直す。変わる子だけを渡した順で返す。
func retarget(parentID string, moved map[int]int, rows []row) []retargeted {
	rename := map[string]string{}
	for _, r := range rows {
		child, ok := ids.ParseChild(r.ticket)
		n, isInt := r.phase.(int)
		if !ok || child.Parent != parentID || !isInt {
			continue
		}
		if to, found := moved[n]; found && to != n {
			rename[r.ticket] = ids.ChildID(parentID, to, child.Seq)
		}
	}
	var out []retargeted
	for _, r := range rows {
		newTicket, renamed := rename[r.ticket]
		if !renamed {
			newTicket = r.ticket
		}
		newPhase := r.phase
		if renamed {
			newPhase = moved[r.phase.(int)]
		}
		newPreds := make([]string, len(r.predecessors))
		for i, p := range r.predecessors {
			newPreds[i] = cmp.Or(rename[p], p)
		}
		if newTicket != r.ticket || !slices.Equal(newPreds, r.predecessors) {
			out = append(out, retargeted{r.ticket, newTicket, newPhase, newPreds})
		}
	}
	return out
}

func changesOf(s *Source, moved map[int]int) []childChange {
	files := slices.Concat(s.children, s.referrers)
	rows := make([]row, len(files))
	byID := map[string]file{}
	for i, f := range files {
		rows[i] = row{f.ticket.ID, phaseOf(f.ticket), slices.Clone(f.ticket.Predecessors)}
		byID[f.ticket.ID] = f
	}
	var out []childChange
	for _, r := range retarget(s.Parent.ID, moved, rows) {
		f := byID[r.ticket]
		out = append(out, childChange{f.ticket, f.raw, r.newTicket, r.newPhase, r.newPredecessors})
	}
	return out
}

// childProblemsOf は付け替えられない理由。承認済みの側（承認済みチケット・記録・マーカー・フロー・ワークツリー）は
// エージェントが直せないので、見つけたら書かない（直すのはユーザ）。
func childProblemsOf(s *Source, changes []childChange) []string {
	var renamed []childChange
	for _, c := range changes {
		if c.renamed() {
			renamed = append(renamed, c)
		}
	}
	if len(renamed) == 0 {
		return nil
	}
	// 付け替え先と名前がぶつかるものを、見つけた順に集める。ぶつかる相手は todo/ の提案、ほかの置き場の提案、フローの下書き、
	// 承認済みチケット、ワークツリー、承認済みの記録の順。
	// leaving は今回の付け替えで元の名前が空くもの。空く名前は、別の子の付け替え先になってもぶつからない。
	// 付け替え先と名前がぶつくものを、見つけた順に集める。ぶつかる相手は todo/ の提案、ほかの置き場の提案、フローの下書き、
	// 承認済みチケット、ワークツリー、承認済みの記録の順。
	// leaving は今回の付け替えで元の名前が空くもの。空く名前は、別の子の付け替え先になってもぶつからない。
	conf, root := s.conf, s.root
	leaving := map[string]bool{}
	leavingPaths := map[string]bool{}
	for _, c := range renamed {
		leaving[c.ticket.ID] = true
		leavingPaths[ospath.Realpath(c.ticket.Path)] = true
	}
	hitsLeaving := func(preds []string) []string {
		var hit []string
		for _, p := range preds {
			if leaving[p] && !slices.Contains(hit, p) {
				hit = append(hit, p)
			}
		}
		slices.Sort(hit)
		return hit
	}
	var found []string
	for _, c := range renamed {
		next := c.newTicket
		if leaving[next] {
			continue
		}
		if fsio.Lexists(c.newPath()) && !leavingPaths[ospath.Realpath(c.newPath())] {
			found = append(found, c.ticket.ID+" の付け替え先 "+next+" のファイルが既にある（"+c.newPath()+"）")
		} else if slices.ContainsFunc(s.proposals, func(t *ticket.Ticket) bool { return t.ID == next }) {
			found = append(found, c.ticket.ID+" の付け替え先 "+next+" の提案が既にある")
		}
	}
	// 名前（元の識別子と付け替え先）を指す他の置き場の提案を探す。todo/ の提案は今回まとめて書き換えるので除く。
	names := map[string]bool{}
	for id := range leaving {
		names[id] = true
	}
	for _, c := range renamed {
		names[c.newTicket] = true
	}
	for _, t := range s.proposals {
		if t.State == place.Todo {
			continue
		}
		if names[t.ID] || len(hitsLeaving(t.Predecessors)) > 0 {
			found = append(found, string(t.State)+"/ の提案 "+t.ID+" が付け替える識別子を指す（"+t.Path+"）")
		}
	}
	// 設定が無ければ、置き場の外（下書き・承認済み・ワークツリー）は見られないので、ここまでの結果で返す。
	if conf == nil {
		return sortedUnique(found)
	}
	for _, c := range renamed {
		draft := flow.DraftFile(conf, cmp.Or(c.ticket.TreeRoot, root), c.newTicket)
		if !leaving[c.newTicket] && fsio.Lexists(draft) {
			found = append(found, c.ticket.ID+" の付け替え先のフローの下書きが既にある（"+draft+"）")
		}
	}
	for _, t := range approval.ReadRaw(conf, root).Everything() {
		if names[t.ID] {
			found = append(found, "承認済みチケット "+t.ID+" が付け替える識別子と同じ（"+t.Path+"）")
		}
		if hit := hitsLeaving(t.Predecessors); len(hit) > 0 {
			found = append(found, "承認済みチケット "+t.ID+" の predecessors が付け替える "+strings.Join(hit, ", ")+" を指す")
		}
	}
	// 全ツリー（ワークスペースルートとワークツリー）の承認済みの側を調べる。
	// フローと phase マーカーは `<識別子>.` で始まる名前で置かれるので、前方一致で拾う。
	sortedNames := make([]string, 0, len(names))
	for id := range names {
		sortedNames = append(sortedNames, id)
	}
	slices.Sort(sortedNames)
	for _, tr := range tree.AllTrees(root, conf.Projects) {
		if tr.Name != "" && names[tr.Name] {
			found = append(found, "ワークツリー "+tr.Name+" が付け替える識別子と同じ名前（"+tr.Root+"）")
		}
		at := conf.ApprovedDir(tr.Root)
		marks := ospath.Join(at, place.PhasesDir, s.Parent.ID)
		listed, err := fsio.Listdir(marks)
		if err != nil {
			listed = nil
		}
		for _, id := range sortedNames {
			paths := []string{ospath.Join(at, flow.FlowsDir, id+flow.Suffix)}
			for _, n := range listed {
				if strings.HasPrefix(n, id+".") {
					paths = append(paths, ospath.Join(marks, n))
				}
			}
			for _, path := range paths {
				if fsio.Lexists(path) {
					rel, _ := ospath.Relpath(path, tr.Root)
					found = append(found, "承認済みの側に "+id+" の記録がある（"+cmp.Or(tr.Name, "ワークスペースルート")+": "+rel+"）")
				}
			}
		}
	}
	return sortedUnique(found)
}

func sortedUnique(found []string) []string {
	out := slices.Clone(found)
	slices.Sort(out)
	return slices.Compact(out)
}

// mismatched は todo/ の子の提案のうち、phase: が親の計画の番号に無いもの（親と子の食い違い）。
func mismatched(s *Source) []any {
	count := len(s.Parent.Numbered())
	out := []any{}
	for _, c := range s.children {
		t := c.ticket
		if !t.IsChild() || (1 <= t.Phase && t.Phase <= count) {
			continue
		}
		out = append(out, obj("ticket", t.ID, "path", t.Path, "phase", t.Phase,
			"text", fmt.Sprintf("%s の phase: %d は親の計画（1〜%d 番）にありません。", t.ID, t.Phase, count)))
	}
	return out
}

// Reorder は線（order。nil なら提案のまま）から答えを組む。
func Reorder(s *Source, order Order) *Answer {
	parent := s.Parent
	got := renumber(parent, order, s.fixed)
	revised := *parent
	revised.Plan, revised.Feedback = got.plan, got.feedback
	revised.Workflow = nil
	revised.Workflow = phases.Compute(&revised)
	types := phases.TypesOf(parent)
	revised.PhaseTypes = types
	// 振り直したステップは組み直すので、提案に元からある after の形の誤りが消える。振り直す前の誤りも入れる
	// （読めない after を書いた提案を、線を引き直しただけで通さない）。
	var redrawn []phases.Part
	if len(got.refused) == 0 {
		for _, k := range parts {
			if _, ok := order[k]; ok {
				redrawn = append(redrawn, k)
			}
		}
	}
	problems := errorsOnly(phases.PlanProblems(&revised, types, ""))
	for _, k := range redrawn {
		problems = append(problems, phases.AfterProblems(parent, k)...)
	}
	var lock []rules.Problem
	var changes []childChange
	if len(got.refused) == 0 {
		if s.current != nil {
			lock = candidates.LockProblems(&revised, s.current, s.fixed, got.moved)
		}
		changes = changesOf(s, got.moved)
	}
	return &Answer{s, got, &revised, problems, lock, changes, childProblemsOf(s, changes), redrawn}
}

func errorsOnly(ps []rules.Problem) []rules.Problem {
	var out []rules.Problem
	for _, p := range ps {
		if p.Severity == rules.Error {
			out = append(out, p)
		}
	}
	return out
}

// plans は図に描く中身。計画ごとに 1 件（全体計画とフィードバック計画）。
func (a *Answer) plans() []any {
	parent, revised := a.source.Parent, a.revised
	// back は振り直した番号 → 元の番号。図は新しい番号で描き、from で元の番号を示す。
	// held は今承認されている改版前の状態（current）の進み具合で、図の current に使う。
	wf := cmp.Or(revised.Workflow, phases.Compute(revised))
	types := phases.TypesOf(parent)
	back := map[int]int{}
	for old, renumbered := range a.renumbered.moved {
		back[renumbered] = old
	}
	loose, ready := phases.Loose(revised, wf), phases.Ready(revised, wf)
	current := a.source.current
	var held *ticket.Workflow
	if current != nil {
		held = phases.Compute(current)
	}
	sha := a.source.sha()
	original := map[int]*ticket.PlanItem{}
	for _, step := range parent.Numbered() {
		original[step.Number] = step.Item
	}
	out := []any{}
	for _, s := range sectionsOf(revised) {
		numbers := s.numbers()
		in := func(n int) bool { return slices.Contains(numbers, n) }
		items := []any{}
		after, proposed := obj(), obj()
		for i, n := range numbers {
			item := s.items[i]
			pt := types.Get(item.Type)
			typeReview := ticket.Unsaid
			title := item.Type
			if pt != nil {
				typeReview = ticket.NeedOf(pt.Review)
				title = cmp.Or(pt.Title, item.Type)
			}
			reviewAt, ok := wf.ReviewAt[n]
			items = append(items, obj("number", n, "from", cmp.Or(back[n], n), "type", item.Type, "title", title,
				"review", needJSON(ticket.ChosenReview(item.Review, typeReview)), "deferred", item.Deferred,
				"review_at", optional(reviewAt, ok), "locked", a.source.fixed[n]))
			if len(item.After) > 0 {
				after.Set(strconv.Itoa(n), ints(item.After))
			}
			if o := original[n]; o != nil && len(o.After) > 0 {
				p := slices.Clone(o.After)
				slices.Sort(p)
				proposed.Set(strconv.Itoa(n), ints(slices.Compact(p)))
			}
		}
		var currentJSON any
		if held != nil {
			currentJSON = direct(current, held, s.key)
		}
		var problems []rules.Problem
		problems = append(problems, phases.PlanProblems(revised, types, s.key)...)
		if slices.Contains(a.redrawn, s.key) {
			problems = append(problems, phases.AfterProblems(parent, s.key)...)
		}
		out = append(out, obj("ticket", parent.ID, "part", string(s.key), "source_sha", sha, "items", items,
			"after", after, "proposed", proposed, "current", currentJSON,
			"loose", ints(filter(loose, in)), "ready", ints(filter(ready, in)),
			"problems", problemTexts(errorsOnly(problems))))
	}
	return out
}

func filter(ns []int, keep func(int) bool) []int {
	var out []int
	for _, n := range ns {
		if keep(n) {
			out = append(out, n)
		}
	}
	return out
}

// direct は承認済みの先行関係から戻した直接の先行（推移的に辿れる線を除く）。その計画の番号だけ。
func direct(current *ticket.Ticket, wf *ticket.Workflow, part phases.Part) *jsonObject {
	out := obj()
	for _, s := range sectionsOf(current) {
		if s.key != part {
			continue
		}
		numbers := s.numbers()
		for _, n := range numbers {
			waits := filter(wf.Waits[n], func(m int) bool { return slices.Contains(numbers, m) })
			found := filter(waits, func(m int) bool {
				return !slices.ContainsFunc(waits, func(k int) bool { return slices.Contains(wf.Waits[k], m) })
			})
			if len(found) > 0 {
				out.Set(strconv.Itoa(n), ints(found))
			}
		}
		return out
	}
	return out
}

// Body は `--plan-order <親> --json` の答え。entry が written と source_sha を足す（plans の各要素の source_sha も）。
func (a *Answer) Body() *jsonObject {
	refused := []any{}
	for _, r := range a.renumbered.refused {
		refused = append(refused, r.json())
	}
	children := []any{}
	for i := range a.changes {
		children = append(children, a.changes[i].json())
	}
	return obj("version", version, "ticket", a.source.Parent.ID, "source_sha", a.source.sha(), "plans", a.plans(),
		"problems", problemTexts(a.problems), "loose", ints(phases.Loose(a.revised, a.revised.Workflow)), "refused", refused,
		"revision_problems", problemTexts(a.revisionProblems), "children", children,
		"child_problems", strs(a.childProblems), "mismatched", mismatched(a.source), "writable", a.writable())
}

// Parent は承認の対象に入った計画がある親の提案と、改版ならその承認済みチケット。
type Parent struct {
	Proposal, Current *ticket.Ticket
}

// PreviewPlans は承認の preview に載せる plans。--order は渡さない（承認する提案そのものの順序）。
func PreviewPlans(conf *config.Settings, root string, parents []Parent, proposals []*ticket.Ticket) []any {
	out := []any{}
	for _, p := range parents {
		if s, _ := Load(conf, root, p.Proposal, proposals, p.Current); s != nil {
			out = append(out, Reorder(s, nil).plans()...)
		}
	}
	return out
}
