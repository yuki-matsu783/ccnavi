package candidates

import (
	"cmp"
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/model/ids"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/standing"
	"ccnavi/internal/tickets/model/ticket"
)

// ParentElsewhereText は子の親が承認済みの一覧に無いのに、別のツリーに同じ識別子が在るときの案内（Validate の
// missing に渡す）。無ければ空。判定は変えない。everything は承認済みチケットの全部（nil ならここで読む）。
func ParentElsewhereText(conf *config.Settings, root string, t *ticket.Ticket, everything []*ticket.Ticket) string {
	if !t.IsChild() || t.Parent == "" {
		return ""
	}
	if everything == nil {
		everything = approval.ReadRaw(conf, root).Everything()
	}
	found, _ := ticket.ScanAll(root, conf.Tickets, conf.Projects)
	var places []string
	for _, u := range slices.Concat(everything, found) {
		if u.ID != t.Parent || u.IsChild() || u.Tree == t.Tree {
			continue
		}
		where := cmp.Or(u.Tree, "(ワークスペースルート)") + " の " + string(cmp.Or(u.State, place.Doing)) + "/"
		if !slices.Contains(places, where) {
			places = append(places, where)
		}
	}
	if len(places) == 0 {
		return ""
	}
	return "親 " + t.Parent + " が承認されていない。親 " + t.Parent + " は " + strings.Join(places, ", ") + " に在る。" +
		"親のワークツリーは " + t.Parent + " と同名にして、その中に承認済みチケットを置く"
}

// ExistingBranchWarnings は新規の提案（親）の識別子になるブランチ（"/" を "-" にして識別子と一致するもの）が
// 手元か origin に既にあるときの文。提案がそのブランチの上で書かれている（自分の識別子の名前のワークツリーが
// そのブランチを checkout していて、提案がその中にある）ときは言わない。settled（承認済み・閉じた・レビュー中）の
// 識別子は新規ではないので見ない。
func ExistingBranchWarnings(conf *config.Settings, root string, proposals, settled []*ticket.Ticket) []string {
	done := map[string]bool{}
	for _, t := range settled {
		done[t.ID] = true
	}
	var found []string
	cache := map[string]map[string]bool{}
	said := map[string]bool{}
	for _, t := range proposals {
		if t.State != place.Todo || t.IsChild() || done[t.ID] || said[t.ID] {
			continue
		}
		said[t.ID] = true
		if ownBranch(conf, root, t) != "" {
			continue
		}
		repo := root
		if t.Project != "" {
			repo = tree.ProjectRoot(conf.Projects, t.Project)
		}
		if _, ok := cache[repo]; !ok {
			cache[repo] = git.BranchNames(repo, git.StateTimeout)
		}
		folded := pyfmt.Casefold(t.ID)
		var hits []string
		for name := range cache[repo] {
			if pyfmt.Casefold(ids.BranchToID(name)) == folded {
				hits = append(hits, name)
			}
		}
		slices.Sort(hits)
		if len(hits) > 0 {
			found = append(found, t.ID+": 親のブランチ名と同じブランチ（"+strings.Join(hits, ", ")+"）が既にある。"+
				"承認すると、そのブランチを親のブランチとして取り込み、push する。"+
				"別の作業なら識別子を変える")
		}
	}
	return found
}

// ownBranch は提案が自分の識別子の名前のワークツリーの中にあるとき、そのワークツリーの HEAD のブランチ
// （変換したものが識別子と一致するときだけ）。そうでなければ空。
func ownBranch(conf *config.Settings, root string, t *ticket.Ticket) string {
	if t.Path == "" {
		return ""
	}
	here, ok := tree.TreeOf(root, t.Path, conf.Projects)
	if !ok || here.IsMain() || here.Name != t.ID {
		return ""
	}
	return standing.HeadBranch(here.Root, t.ID)
}
