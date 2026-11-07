package lint

import (
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/model/standing"
	"ccnavi/internal/tickets/model/ticket"
)

// FamilyCheck は取り込みの後の検査（`ccnavi sync check <P> [<リポジトリ>]`）。この親子のチケットの承認済みチケットを
// 取り込みのあとに判定し直し、正とする側の検査（親のワークツリーの外のチケット・決まらない）とあわせて、止める理由を
// error で返す。手元のレイヤーが origin/<統合先> と違うことは warn（origin/<統合先> が手元に無ければ黙る）。何も書かない
// （error の扱いは sh が決める）。repo は self かプロジェクト名で、空なら同じ名前の親のワークツリーのあるリポジトリを
// 全部探す。
func FamilyCheck(conf *config.Settings, root, family, repo string) []rules.Problem {
	fams := standing.NewFamilies(conf, root)
	var st standing.Standing
	if repo == "" {
		st = fams.StandingAny(family)
	} else {
		st = fams.Standing(family, tree.ProjectOfKey(repo))
	}
	raw := approval.ReadRaw(conf, root)
	copies, _ := approval.Scan(conf, root, false, raw)
	closed, _ := approval.Scan(conf, root, true, raw)
	review, _ := approval.ScanReview(conf, root, raw)
	ours := func(t *ticket.Ticket) bool {
		home := t.Parent
		if home == "" {
			home = t.ID
		}
		return home == family && tree.RepoKey(t.Project) == st.Repo
	}
	index := approval.ByID(copies)
	var problems []rules.Problem
	for _, t := range copies {
		if !ours(t) {
			continue
		}
		// チケットごとに判定し直し、error がどのチケットのものかを構造で持つ（文面の書式に頼らない）。
		for _, p := range copyProblems(conf, root, []*ticket.Ticket{t}, index, closed, nil) {
			if p.Severity == rules.Error {
				problems = append(problems, p)
			}
		}
	}
	// 親のワークツリーの中の読めないチケットも止める理由（読めないチケットは一覧に入らず、判定し直しから外れる）。
	if st.Home != nil {
		for _, u := range approval.UnreadableCopies(conf, st.Home.Root) {
			problems = append(problems, ticketProblem(rules.Error, u.Message))
		}
	}
	for _, t := range review {
		if ours(t) && t.Blocked != "" {
			problems = append(problems, ticketProblem(rules.Error, t.ID+": "+t.Blocked))
		}
	}
	if st.Stop != "" {
		problems = append(problems, rules.Problem{Severity: rules.Error, Rule: "(family/" + st.Repo + "/" + family + ")", Detail: st.Stop})
	}
	return append(problems, layerDrift(conf, root, tree.ProjectOfKey(st.Repo))...)
}
