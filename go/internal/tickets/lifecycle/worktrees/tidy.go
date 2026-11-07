package worktrees

import (
	"fmt"
	"io"
	"maps"
	"slices"
	"sort"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/model/ids"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/phase"
)

// TidyTargets は片付けの候補。閉じた（done/ の。完了も取り消しも）子のうち、phaseNo が 0 でなければそのフェーズ
// （延期を引き受けた分を含む）の子。作業中・レビュー待ちの子は入らない。識別子の順で、重複は 1 つにする。
func TidyTargets(conf *config.Settings, root, family string, phaseNo int) []*ticket.Ticket {
	closed, _ := approval.Scan(conf, root, true, nil)
	children := approval.ChildrenOf(closed, family)
	if phaseNo != 0 {
		numbers := map[int]bool{phaseNo: true}
		for _, p := range phase.Phases(conf, root, family, nil) {
			if p.Number == phaseNo {
				for _, n := range p.Covers() {
					numbers[n] = true
				}
			}
		}
		children = slices.DeleteFunc(slices.Clone(children), func(t *ticket.Ticket) bool { return !numbers[t.Phase] })
	}
	sorted := slices.Clone(children)
	sort.SliceStable(sorted, func(i, j int) bool { return sorted[i].ID < sorted[j].ID })
	seen := map[string]bool{}
	var out []*ticket.Ticket
	for _, t := range sorted {
		if !seen[t.ID] {
			seen[t.ID] = true
			out = append(out, t)
		}
	}
	return out
}

// FamilyFor は識別子が属する親子の親。チケットが見つかればその parent（親なら自身）を採り、見つからないときだけ
// 名前の形で割り出す。親の識別子が子の形（`rel-2026-10-06`）で終わっていても末尾を剥がして別の親と読み違えないため。
func FamilyFor(conf *config.Settings, root, ident string) string {
	raw := approval.ReadRaw(conf, root)
	open, _ := approval.Scan(conf, root, false, raw)
	closed, _ := approval.Scan(conf, root, true, raw)
	review, _ := approval.ScanReview(conf, root, raw)
	for _, t := range slices.Concat(open, closed, review) {
		if t.ID == ident {
			if t.Parent != "" {
				return t.Parent
			}
			return t.ID
		}
	}
	if c, ok := ids.ParseChild(ident); ok {
		return c.Parent
	}
	return ident
}

// RunTidy は `worktree tidy <親> [--phase <N>]`（phaseNo の 0 は指定なし）。閉じた子のワークツリーを消す。
// 消さなかったものが 1 本でもあれば 1。何も無ければ黙って 0。
func RunTidy(stdout io.Writer, conf *config.Settings, root, family string, phaseNo int, cwd string) int {
	code := 0
	family = FamilyFor(conf, root, family)
	for _, t := range TidyTargets(conf, root, family, phaseNo) {
		if !ospath.DiskLexists(worktreePath(root, t.ID)) {
			continue
		}
		d := Drop(root, t.ID, cwd)
		Report(stdout, root, d, AfterDropped)
		if !d.Removed() {
			code = 1
		}
	}
	return code
}

// left は ready の後に残ったワークツリー 1 本。why は残った理由、check は消す前に確かめること。
type left struct {
	name, path string
	why, check []string
}

// leftoverFacts は残ったワークツリーが抱えているもの（Drop が Dirty で止める条件と同じ）。消さずに読むだけ。
func leftoverFacts(path string) (why, check []string) {
	out, ok := status(path)
	if !ok {
		return nil, nil
	}
	var changes []string
	for _, line := range ospath.Splitlines(out) {
		if ospath.Strip(line) != "" {
			changes = append(changes, line)
		}
	}
	if len(changes) == 0 {
		return nil, nil
	}
	scratch := slices.ContainsFunc(changes, func(c string) bool {
		return strings.Contains(strings.ReplaceAll(c, `\`, "/"), "wip/handoff/")
	})
	if scratch {
		why = append(why, "未保存の下書きがある（wip/handoff/）")
	} else {
		why = append(why, "未コミットの変更がある")
	}
	check = append(check, "`git -C "+shell.Quote(path)+" status` で中身を見て、必要なものはコミットするか退避する")
	return why, check
}

// remaining は ready の最後の `drop <親>` の後に、まだ残っている親子のワークツリー。見るだけで消さない。
// 名前が親と違うがその親のチケットを持つワークツリー（drop は名前で消すので拾えない）と、未保存の下書きがあって
// Drop が消さなかった閉じた子のワークツリー。
func remaining(conf *config.Settings, root, family string) []left {
	family = FamilyFor(conf, root, family)
	open, _ := approval.ScanAll(conf, root, false)
	closed, _ := approval.ScanAll(conf, root, true)
	review, _ := approval.ReviewAll(conf, root)
	proposals, _ := ticket.ScanAll(root, conf.Tickets, conf.Projects)
	var mine []*ticket.Ticket
	members := map[string]bool{}
	for _, t := range slices.Concat(open, closed, review, proposals) {
		owner := t.Parent
		if owner == "" {
			owner = t.ID
		}
		if owner == family {
			mine = append(mine, t)
			members[t.ID] = true
		}
	}
	holders := map[string]string{}
	for _, t := range mine {
		if _, held := holders[t.Tree]; t.Tree != "" && !members[t.Tree] && !held {
			holders[t.Tree] = t.TreeRoot
		}
	}
	var found []left
	for _, name := range slices.Sorted(maps.Keys(holders)) {
		path := worktreePath(root, name)
		if !ospath.IsDir(path) || ospath.IsLink(path) {
			continue
		}
		branch, _ := tree.BranchOf(holders[name])
		named := ""
		if branch != "" {
			named = "ブランチ " + branch + " の"
		}
		why := []string{"名前が親 " + family + " と違うので、親の片付けの対象にならなかった"}
		check := []string{named + "作業が push 済みで、ほかのセッションが使っていないことを確かめる"}
		moreWhy, moreCheck := leftoverFacts(path)
		found = append(found, left{name, path, append(why, moreWhy...), append(check, moreCheck...)})
	}
	for _, t := range TidyTargets(conf, root, family, 0) {
		path := worktreePath(root, t.ID)
		if t.ID == family || !ospath.IsDir(path) || ospath.IsLink(path) {
			continue
		}
		if owner, _ := Owner(path); owner == "" {
			continue
		}
		if why, check := leftoverFacts(path); len(why) > 0 {
			found = append(found, left{t.ID, path, why, check})
		}
	}
	return found
}

// RunRemaining は `worktree remaining <親>`。残りを一覧にする。出すだけで消さない。無ければ何も出さない。
func RunRemaining(stdout io.Writer, conf *config.Settings, root, family string) int {
	found := remaining(conf, root, family)
	if len(found) == 0 {
		return 0
	}
	fmt.Fprint(stdout, "残っているワークツリー（自動では削除していません。不要なら確かめてから削除します）:\n")
	for _, item := range found {
		fmt.Fprintf(stdout, "- %s（%s）\n", item.name, item.path)
		fmt.Fprintf(stdout, "  残った理由: %s\n", strings.Join(item.why, "。"))
		for _, line := range item.check {
			fmt.Fprintf(stdout, "  削除する前に確かめること: %s\n", line)
		}
		fmt.Fprintf(stdout, "  削除するコマンド: %s\n", retryCommand(root, item.name))
	}
	return 0
}

// ChildWorktrees は承認した子それぞれのワークツリーの計画。Create の子は拡張が
// `ccnavi-git.sh worktree add <path> -b <子> <base>` で作る。base は親のワークツリーが今チェックアウトしている
// ブランチで、親が `feature/123-login` のように "/" を含むときもそこから切れる。
func ChildWorktrees(root string, tickets []*ticket.Ticket) []ticket.WorktreePlan {
	var out []ticket.WorktreePlan
	for _, t := range tickets {
		if !t.IsChild() {
			continue
		}
		p := ticket.WorktreePlan{Ticket: t.ID, Parent: t.Parent, Path: ospath.Slashed(tree.WorktreesDir) + "/" + t.ID, Branch: t.ID}
		switch {
		case t.Project != "":
			p.State = ticket.WorktreeProject
		case ospath.DiskExists(worktreePath(root, t.ID)):
			p.State = ticket.WorktreeExists
		default:
			if base := checkedOut(root, worktreePath(root, t.Parent)); base != "" {
				p.Base, p.State = base, ticket.WorktreeCreate
			} else {
				p.State = ticket.WorktreeNoParent
			}
		}
		out = append(out, p)
	}
	return out
}

// checkedOut は親のワークツリー（path）が今チェックアウトしているブランチ名。無い・読めないなら空。
func checkedOut(root, path string) string {
	if !ospath.IsDir(path) || !tree.IsWorktreeOf(root, path) {
		return ""
	}
	code, out := git.Output(path, []string{"rev-parse", "--abbrev-ref", "HEAD"}, git.Options{Timeout: git.CleanupTimeout})
	name := ospath.Strip(out)
	if code != 0 || name == "" || name == "HEAD" {
		return ""
	}
	return name
}
