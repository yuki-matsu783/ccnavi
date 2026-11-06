package phase

import (
	"slices"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/approval/marks"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/model/ticket"
)

// Phases はこの親のフェーズを番号順に。開いている承認済みチケットと閉じた承認済みチケットの両方から組み、
// 親が計画を持てばまだ子の無い番号も並ぶ。raw は呼び手が approval.ReadRaw で読んだ置き場で、読んでから
// 置き場のファイルを動かしていないときだけ渡す（nil ならここで読む）。
func Phases(conf *config.Settings, root, parentID string, raw *approval.Raw) []*Phase {
	return phasesOf(conf, root, parentID, nil, raw)
}

// phasesOf の proposed は、承認済みチケットがまだ無いときに計画を読む親（同じ承認で通る親の提案）。渡さないと、
// 親と後のフェーズの子を一緒に承認したときフェーズが 1 つも並ばず、順序の検査が何も見ないまま通る。
func phasesOf(conf *config.Settings, root, parentID string, proposed *ticket.Ticket, raw *approval.Raw) []*Phase {
	if raw == nil {
		raw = approval.ReadRaw(conf, root)
	}
	opened, _ := approval.Scan(conf, root, false, raw)
	closed, _ := approval.Scan(conf, root, true, raw)
	byNumber := map[int]*Phase{}
	owner := approval.ByID(slices.Concat(opened, closed))[parentID]
	if owner == nil && proposed != nil && proposed.ID == parentID {
		owner = proposed
	}
	if owner != nil && owner.HasPlan() {
		// 定義は親に固定したコピー（phases:）から引く。承認のあとに phases.yml を直しても、進行中の親の
		// フェーズは変わらない。
		types := phases.TypesOf(owner)
		if owner.Workflow == nil {
			computed := *owner
			computed.Workflow = phases.Compute(owner)
			owner = &computed
		}
		for _, s := range owner.Numbered() {
			p := newPhase(parentID, s.Number)
			p.Item, p.Type, p.Owner = s.Item, types.Get(s.Item.Type), owner
			byNumber[s.Number] = p
		}
		for _, p := range byNumber {
			for _, c := range p.Covers() {
				p.CoveredReviews = append(p.CoveredReviews, coveredReview(byNumber[c]))
			}
		}
	}
	// 状態は置き場そのもの。閉じた・レビュー待ち・作業中の順に読み、同じ識別子は閉じた側を採る。エージェントが
	// 書ける todo/ に同じ識別子を書いても、フェーズは開き直らない。
	review, _ := approval.ScanReview(conf, root, raw)
	seen := map[string]bool{}
	for _, pool := range [][]*ticket.Ticket{closed, review, opened} {
		for _, t := range approval.ChildrenOf(pool, parentID) {
			// 統合の子は計画に載らないので、フェーズの進行・レビューの対象に含めない。
			n, child := t.ChildPhase()
			if !child || seen[t.ID] || t.IsConsolidate() {
				continue
			}
			seen[t.ID] = true
			p := byNumber[n]
			if p == nil {
				p = newPhase(parentID, n)
				byNumber[n] = p
			}
			p.Tickets = append(p.Tickets, t)
			p.States[t.ID] = t.State
		}
	}
	// マーカーと記録は親のツリーに置く。子の側に書くと、同じフェーズのマーカーが複数のツリーに分かれる。
	var where string
	if owner != nil {
		where = approval.HomeDirRead(conf, root, parentID, "", owner.Project)
	} else {
		where = approval.HomeDirReadAny(conf, root, parentID, "")
	}
	numbers := make([]int, 0, len(byNumber))
	for n, p := range byNumber {
		numbers = append(numbers, n)
		p.Marks = marks.Marks(where, parentID, n)
		for _, t := range p.Tickets {
			if record, ok := marks.ReadRecord(where, parentID, t.ID, marks.Risk); ok && record.Len() > 0 {
				p.Risks[t.ID] = record
			}
		}
	}
	slices.Sort(numbers)
	out := make([]*Phase, 0, len(numbers))
	for _, n := range numbers {
		out = append(out, byNumber[n])
	}
	return out
}

// find は番号のフェーズ。無ければ nil。
func find(list []*Phase, number int) *Phase {
	for _, p := range list {
		if p.Number == number {
			return p
		}
	}
	return nil
}

// HeldPhase はレビューが済むまで止めているフェーズ。無ければ nil。raw は Phases と同じ。
func HeldPhase(conf *config.Settings, root, parentID string, raw *approval.Raw) *Phase {
	for _, p := range Phases(conf, root, parentID, raw) {
		if p.GateClosed() {
			return p
		}
	}
	return nil
}

// WorktreeAt は cwd が入っているワークツリーかプロジェクト。ワークスペースルートか外なら偽。cwd が空なら
// 今の作業ディレクトリ。偽なら置き場を読まずに済む（ParentAt）。
func WorktreeAt(conf *config.Settings, root, cwd string) (tree.Tree, bool) {
	if cwd == "" {
		cwd, _ = ospath.Getwd()
	}
	t, ok := tree.TreeOf(root, cwd, conf.Projects)
	if !ok || t.IsMain() {
		return tree.Tree{}, false
	}
	return t, true
}

// ParentAt は cwd が親のワークツリーの中ならその親の承認済みチケットと、引くのに使った置き場。置き場を読むのは
// cwd がワークツリーかプロジェクトの中で raw が nil のときだけで、読んだ置き場を返すので呼び手は同じ hook の
// 残りに持ち回れる。cwd がワークスペースルートか外なら raw をそのまま返す。
//
// 起動とシェルには書き込み先が無いので、止める親は cwd で引く。cd 1 回で外れるが、外れた先で起動した
// サブエージェントの書き込みは書き込み先で止まる。
func ParentAt(conf *config.Settings, root, cwd string, raw *approval.Raw) (*ticket.Ticket, *approval.Raw) {
	here, ok := WorktreeAt(conf, root, cwd)
	if !ok {
		return nil, raw
	}
	if raw == nil {
		raw = approval.ReadRaw(conf, root)
	}
	// 承認は親のワークツリーを作る前にも打てるので、承認済みチケットはプロジェクトのルートに在ることもある。
	opened, _ := approval.Scan(conf, root, false, raw)
	found, ok := tree.Lookup(approval.ByID(opened), here.Name)
	if !ok || found.IsChild() {
		return nil, raw
	}
	return found, raw
}
