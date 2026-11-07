package integrate

import (
	"path/filepath"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/model/archive"
	"ccnavi/internal/tickets/model/directcand"
	"ccnavi/internal/tickets/model/ids"
	"ccnavi/internal/tickets/model/place"
)

// TreeRoots はチケットの置き場を探すツリーの根。登録されたツリーに加え、.claude/worktrees/ とプロジェクトの
// 置き場の直下のディレクトリも、登録の有無によらず入れる（外れたワークツリーの残骸のチケットでも断る向き）。
func TreeRoots(conf *config.Settings, root string) []string {
	var roots []string
	for _, t := range tree.AllTrees(root, conf.Projects) {
		roots = append(roots, t.Root)
	}
	homes := []string{tree.WorktreesPath(root)}
	if conf.Projects != "" {
		if ospath.IsAbs(conf.Projects) {
			homes = append(homes, conf.Projects)
		} else {
			homes = append(homes, ospath.Join(root, conf.Projects))
		}
	}
	for _, home := range homes {
		entries, err := ospath.DiskListdir(home)
		if err != nil {
			continue
		}
		for _, e := range entries {
			if p := ospath.Join(home, e); ospath.IsDir(p) {
				roots = append(roots, p)
			}
		}
	}
	seen := map[string]bool{}
	out := roots[:0]
	for _, r := range roots {
		if !seen[r] {
			seen[r] = true
			out = append(out, r)
		}
	}
	return out
}

// NamedFile はディレクトリの中の <名前>.md（実際の表記のパス）。大文字小文字と NFC は畳んで比べる。無ければ空。
func NamedFile(directory, name string) string {
	want := pathcase.Fold(pyfmt.NFC(name + config.MdExt))
	entries, err := ospath.DiskListdir(directory)
	if err != nil {
		return ""
	}
	for _, e := range entries {
		if pathcase.Fold(pyfmt.NFC(e)) == want {
			return ospath.Join(directory, e)
		}
	}
	return ""
}

// BoundReason はこの名前のワークツリーがチケットに結び付く理由（integrate の判定）。結び付かなければ空。
// 決められないものは結び付かないと読む。Stop の判定は区別する boundCheck を使う。
func BoundReason(conf *config.Settings, root string, here tree.Tree) string {
	reason, _ := boundCheck(conf, root, here, time.Time{})
	return reason
}

// boundCheck は BoundReason の本体。unsure は、理由が無く、origin の追跡ブランチの有無を引く git が期限不足か
// 失敗で終わったとき真。材料は広めに適用して断る向きで、全ツリーの承認済みチケット（doing/・done/）と提案
// （todo/・review/）の <名前>.md、手元の退避の同じ名前の親、origin に在る親のブランチのワークツリー（直接作業の
// 記録があるワークツリーは除く。直接作業のブランチも push で origin へ送れるので、送ると促しが黙って消える）。
func boundCheck(conf *config.Settings, root string, here tree.Tree, deadline time.Time) (reason string, unsure bool) {
	name := here.Name
	owner := tree.ProjectRoot(conf.Projects, here.Project)
	if owner == "" {
		owner = root
	}
	for _, base := range TreeRoots(conf, root) {
		approved := conf.ApprovedDir(base)
		proposals := place.ProposalsRoot(base, conf.Tickets)
		for _, at := range []struct {
			dir   string
			state place.State
		}{{approved, place.Doing}, {approved, place.Done}, {proposals, place.Todo}, {proposals, place.Review}} {
			if found := NamedFile(ospath.Join(at.dir, string(at.state)), name); found != "" {
				rel, ok := ospath.Relpath(found, root)
				if !ok {
					rel = found
				}
				return "チケットか提案（" + filepath.ToSlash(rel) + "）がある", false
			}
		}
	}
	if _, ok := archive.ArchivedFields(root, here.Project, name); ok {
		return "手元の退避（ready 済み）に同じ名前の親がある", false
	}
	cands := []string{name}
	if ident := ids.BranchToID(name); ident != name {
		cands = append(cands, ident)
	}
	for _, cand := range cands {
		work := tree.WorktreePath(root, cand)
		if !ospath.IsDir(work) || ospath.IsLink(work) {
			continue
		}
		branch, ok := tree.BranchOf(work)
		if !ok || branch == "" || pathcase.Fold(ids.BranchToID(branch)) != pathcase.Fold(cand) {
			continue
		}
		if rec := DirectRecord(owner, cand); rec != nil && !rec.broken {
			continue
		}
		done := git.Run(work, []string{"show-ref", "--verify", "--quiet", "refs/remotes/origin/" + branch}, git.Options{Deadline: deadline})
		switch {
		case done.Failure != nil || (done.Code != 0 && done.Code != 1):
			unsure = true
		case done.Code == 0:
			return "親のブランチ（origin/" + branch + "）のワークツリー（" + filepath.ToSlash(tree.WorktreesDir) + "/" + cand + "）がある", false
		}
	}
	return "", unsure
}

// DirectCheck はこのワークツリーが直接作業か。直接作業はブランチがツリーの名前と同じで、名前が統合できる形で、
// チケットに結び付かないもの。対象外なら理由、決められなければ unsure が真、どちらも無ければ直接作業。
// Stop の促し・コミット後の案内・SessionStart の一覧がこの 1 か所を使う。
func DirectCheck(conf *config.Settings, root string, here tree.Tree, deadline time.Time) (reason string, unsure bool) {
	branch, ok := tree.BranchOf(here.Root)
	if !ok {
		return "", true
	}
	if branch != here.Name {
		return "ブランチがワークツリーの名前と違う", false
	}
	if !directcand.IntegrableName(here.Name) {
		return "名前が統合できる形でない", false
	}
	return boundCheck(conf, root, here, deadline)
}
