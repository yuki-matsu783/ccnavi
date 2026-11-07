package ticket

import (
	"slices"

	"ccnavi/internal/tickets/model/place"
)

// projects は並んだチケットのプロジェクトの種類の数。
func projects(lists ...[]*Ticket) int {
	seen := map[string]bool{}
	for _, list := range lists {
		for _, t := range list {
			seen[t.Project] = true
		}
	}
	return len(seen)
}

// home は親のツリーの名前（親自身なら自分の識別子）。
func (t *Ticket) home() string {
	if t.Parent != "" {
		return t.Parent
	}
	return t.ID
}

// fold は同じ識別子のチケットを、正とするツリーの側にまとめる。正とするのは親のツリーの側だけで、子の
// ワークツリーや元ツリーに残ったコピーは正としない。親のツリーに無ければ決めずに全部残す（立ち位置の側が止める）。
// リポジトリをまたいだ衝突はまとめない（別のチケットなので、片方が気づかないうちに消える）。
//
// approved（同じ識別子の承認済みチケットの全部）が在れば、正とするツリーは承認済みチケットだけで決め（Authority）、
// そのツリーの提案だけを残す。承認の前に切ったワークツリーに残った todo/ を承認待ちや改版と読まないため。
func fold(hits, approved []*Ticket) []*Ticket {
	if len(approved) > 0 && projects(approved, hits) > 1 {
		return hits
	}
	if len(approved) == 0 {
		if projects(hits) > 1 || len(hits) == 0 {
			return hits
		}
		home := hits[0].home()
		var atHome []*Ticket
		for _, t := range hits {
			if t.Tree == home {
				atHome = append(atHome, t)
			}
		}
		if len(atHome) > 0 {
			return atHome
		}
		return hits
	}
	where, ok := Authority(approved)
	if !ok {
		return hits
	}
	var out []*Ticket
	for _, t := range hits {
		if t.Tree == where {
			out = append(out, t)
		}
	}
	return out
}

// Authority は同じ識別子の承認済みチケットから、正とするツリーの名前を決める。親のツリーだけを読み、そこに無い
// （親のワークツリーが無い・そこにチケットが無い・リポジトリをまたぐ）なら偽で、ほかのツリーのコピーで代用しない。
// 承認済みチケットと、承認済みの識別子の提案（fold）がこの 1 つで決める。
func Authority(copies []*Ticket) (string, bool) {
	if len(copies) == 0 || projects(copies) > 1 {
		return "", false
	}
	for _, t := range copies {
		if t.Tree == t.home() {
			return t.Tree, true
		}
	}
	return "", false
}

// collidedStates は 1 つのツリーの中で、どれを正とするか決まらない置き場。決まっていれば空。同じ識別子が 2 つの
// 置き場に在るのは動かす途中で止まった形跡。ただし todo/ は親の改版の途中なので、承認済みチケットと並んでよい。
// lint の ERROR とボードの scattered が同じ数え方をする。
func collidedStates(states []place.State) []place.State {
	distinct := slices.Clone(states)
	slices.Sort(distinct)
	distinct = slices.Compact(distinct)
	if len(distinct) > 1 && !slices.Contains(distinct, place.Todo) {
		return distinct
	}
	if len(states) > 1 && len(distinct) < len(states) {
		return states
	}
	return nil
}

// Collisions はどれを正とするか決まらないチケットの全部。決まっていれば空。まとめて 2 つ以上残り、同じツリーの
// 中で collidedStates に当たるときだけ入る（状態の操作が止まるのと lint が言うのとで同じ条件）。acrossTrees は
// 親のツリーに無く、ほかのツリーのコピーだけが残る形も数える（閉じたものだけなら数えない）。
func Collisions(hits []*Ticket, acrossTrees bool) []*Ticket {
	folded := fold(hits, nil)
	if len(folded) < 2 {
		return nil
	}
	trees := map[string]bool{}
	states := make([]place.State, len(folded))
	open := false
	for i, t := range folded {
		trees[t.Tree] = true
		states[i] = t.State
		if t.State.Progress() < place.Done.Progress() {
			open = true
		}
	}
	if len(trees) == 1 {
		if len(collidedStates(states)) > 0 {
			return folded
		}
		return nil
	}
	if acrossTrees && open {
		return folded
	}
	return nil
}

// Group は同じ識別子のチケットの全部。
type Group struct {
	ID      string
	Tickets []*Ticket
}

// ByTicket は識別子ごとのチケットの全部。識別子も中身も見つけた順。
func ByTicket(found []*Ticket) []Group {
	at := map[string]int{}
	var out []Group
	for _, t := range found {
		i, ok := at[t.ID]
		if !ok {
			i = len(out)
			at[t.ID] = i
			out = append(out, Group{ID: t.ID})
		}
		out[i].Tickets = append(out[i].Tickets, t)
	}
	return out
}

// Dedupe は同じ識別子が複数のツリーにあるとき、正とするツリーの側だけを残す。approved（まとめる前の承認済み
// チケットの全部）を渡せば、承認済みの識別子は承認済みチケットで正とするツリーを決める（fold）。渡さないと、
// 正とするツリーの外に残った提案も残る。
func Dedupe(found, approved []*Ticket) []*Ticket {
	settled := map[string][]*Ticket{}
	for _, g := range ByTicket(approved) {
		settled[g.ID] = g.Tickets
	}
	var kept []*Ticket
	for _, g := range ByTicket(found) {
		kept = append(kept, fold(g.Tickets, settled[g.ID])...)
	}
	return kept
}
