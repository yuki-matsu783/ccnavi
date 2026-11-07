package diagnose

import (
	"ccnavi/internal/tickets/config/risk"
)

// markAttention は各チケットに issues（不備の文）と attention（ユーザが動く必要があるか）を足す。ボードはこの 2 つを
// 読むだけにする（判定と同じ答えを 2 か所で出さない）。
func markAttention(rows []*ticketRow, parents []*parentRow, pending map[string]bool) {
	ids := map[string]bool{}
	for _, r := range rows {
		ids[r.id] = true
	}
	byID := map[string]*parentRow{}
	for _, p := range parents {
		byID[p.id] = p
	}
	for _, r := range rows {
		found := issues(r, ids)
		r.obj.Set("issues", found)
		r.obj.Set("attention", attention(r, byID, pending, len(found) > 0))
	}
}

// issues はチケット 1 件の不備の文。並びは、書き込みの止め・提案が無い・親が無い の順。
func issues(r *ticketRow, ids map[string]bool) []string {
	out := []string{}
	if r.blocked != "" {
		out = append(out, "書き込みが止まっています: "+r.blocked)
	}
	if !r.hasProposal && r.status != statusOpen && r.status != statusClosed {
		out = append(out, "提案が見つかりません（承認済みチケットだけがあります）")
	}
	if r.parent != "" && !ids[r.parent] {
		out = append(out, "親 "+r.parent+" が見つかりません")
	}
	return out
}

// attention はチケット 1 件が要対応か。次のどれかで真: 承認待ち（新規と親の改版）、子で自分のフェーズがレビュー準備中・
// レビュー待ち、未着手・作業中の列なのにワークツリーが無い、実績のリスクが HIGH 以上、どれが正か決まらない、不備がある、
// 親でフェーズのどれかが止まっている・レビュー待ち・リスクでレビュー必須。
func attention(r *ticketRow, parents map[string]*parentRow, pending map[string]bool, hasIssues bool) bool {
	isParent := r.parent == ""
	family := parents[r.parent]
	if isParent {
		family = parents[r.id]
	}
	var list []phaseRow
	if family != nil {
		list = family.phases
	}
	var own *phaseRow
	if !isParent {
		for i := range list {
			if list[i].number == r.phase {
				own = &list[i]
				break
			}
		}
	}
	// 提案が無く閉じた承認済みチケットは完了・取り消しの列。それ以外は未着手か作業中の列。
	openColumn := r.hasProposal || r.status != statusClosed
	if pending[r.id] || (own != nil && (own.gateClosed || own.reviewWaiting)) || (openColumn && !r.worktree) ||
		highRisk(r) || r.scattered || hasIssues {
		return true
	}
	if isParent {
		for _, p := range list {
			if p.needsUser() {
				return true
			}
		}
	}
	return false
}

// highRisk は実績のリスクの記録が HIGH 以上か。フェーズの risk_escalates と同じ水準で見る。
func highRisk(r *ticketRow) bool {
	return r.risk != nil && risk.Escalates(risk.Level(r.risk.String("level")))
}
