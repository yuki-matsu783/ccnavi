package notice

import (
	"cmp"
	"maps"
	"slices"
	"strconv"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
)

// noBranch は親のワークツリーがブランチの外（切り離した HEAD）にいるときの言い方。
const noBranch = "（ブランチの外）"

// ChildState は子のワークツリーの状態（`ticket status`）。
type ChildState string

const (
	childMerged   ChildState = "合流済み"
	childUnmerged ChildState = "未合流"
	childDirty    ChildState = "未コミットあり"
)

// Row はこのPCに親のワークツリーがある、まだ閉じていない親 1 本。mr は親チケットの `mr:`（無ければ空）。
// unsent は origin に送っていないものの言い方（無ければ空）。
type Row struct {
	Ident     string
	path      string
	branch    string
	mr        string
	nextChild string
	unsent    string
	project   string
}

// Collect はこのPCに親のワークツリーがある、まだ閉じていない親の一覧。識別子の順。先行の引き方は承認と lint と
// 同じ（PoolOf に AlignImported を当てる）。
func Collect(conf *config.Settings, root string, doing, review, closed, proposals []*ticket.Ticket) []Row {
	// 1. 親を集める。作業中・レビュー中の親と、承認待ち（todo/）の提案の親。同じ識別子は最初のものを採る。
	parents := map[string]*ticket.Ticket{}
	keep := func(t *ticket.Ticket) {
		if _, ok := parents[t.ID]; !ok {
			parents[t.ID] = t
		}
	}
	for _, t := range append(slices.Clone(doing), review...) {
		if !t.IsChild() {
			keep(t)
		}
	}
	for _, t := range proposals {
		if !t.IsChild() && t.State == place.Todo {
			keep(t)
		}
	}
	// 2. このPCにワークツリーがある親だけを、識別子の順に行にする。先行の判定は承認や lint と同じ pool で引く。
	pool := approval.PoolOf(doing, review, closed, proposals)
	pool.AlignImported(conf, root)
	var rows []Row
	for _, ident := range slices.Sorted(maps.Keys(parents)) {
		path := tree.WorktreePath(root, ident)
		if !ospath.IsDir(path) {
			continue
		}
		parent := parents[ident]
		branch, _ := tree.BranchOf(path)
		mr := ""
		if parent.MR != 0 {
			mr = strconv.Itoa(parent.MR)
		}
		rows = append(rows, Row{
			Ident:     ident,
			path:      path,
			branch:    branch,
			mr:        mr,
			nextChild: nextChild(ident, doing, pool),
			unsent:    unsentOf(path, branch),
			project:   parent.Project,
		})
	}
	return rows
}

// byPhase は子をフェーズ・識別子の順に並べる。
func byPhase(a, b *ticket.Ticket) int {
	return cmp.Or(cmp.Compare(a.Phase, b.Phase), cmp.Compare(a.ID, b.ID))
}

// nextChild は次に着手できる子。承認済みで未着手、先行がすべて満たされているもののうち、フェーズ・識別子の最初。
func nextChild(ident string, doing []*ticket.Ticket, pool approval.Pool) string {
	var ready []*ticket.Ticket
	for _, t := range doing {
		if t.Parent == ident && t.StartedAt == "" && t.Blocked == "" && len(approval.UnmetPredecessors(t, pool)) == 0 {
			ready = append(ready, t)
		}
	}
	if len(ready) == 0 {
		return ""
	}
	return slices.MinFunc(ready, byPhase).ID
}

// originRef は手元のリモート追跡の ref（ネットワークには出ない。最後に取ってきた時点の origin）。
func originRef(branch string) string { return "refs/remotes/origin/" + branch }

// unsentOf は origin に送っていないものの言い方。無ければ空。ref が無ければ一度も送っていない。
// 承認済みチケットの未コミットは、`ticket status` が置き場のファイルごとに言う。
func unsentOf(path, branch string) string {
	if branch == "" {
		return ""
	}
	ref := originRef(branch)
	done := git.Run(path, []string{"rev-parse", "--verify", "--quiet", ref}, git.Options{Timeout: git.DefaultTimeout})
	if done.Failure != nil {
		return ""
	}
	if done.Code != 0 {
		return "一度も送っていない"
	}
	counted := git.Run(path, []string{"rev-list", "--count", ref + "..HEAD"}, git.Options{Timeout: git.DefaultTimeout})
	if !counted.OK() {
		return ""
	}
	if n, ok := git.Count(counted.Out); ok && n > 0 {
		return strconv.Itoa(n) + " コミット"
	}
	return ""
}

// divergence は手元の先頭と手元のリモート追跡の ref の差（未送信, 遅れ）。ref が無い・読めなければ ok が偽。
func divergence(path, branch string) (ahead, behind int, ok bool) {
	if branch == "" {
		return 0, 0, false
	}
	done := git.Run(path, []string{"rev-list", "--left-right", "--count", "HEAD..." + originRef(branch)},
		git.Options{Timeout: git.DefaultTimeout})
	if !done.OK() {
		return 0, 0, false
	}
	parts := strings.Fields(done.Out)
	if len(parts) != 2 {
		return 0, 0, false
	}
	ahead, okA := git.Count(parts[0])
	behind, okB := git.Count(parts[1])
	return ahead, behind, okA && okB
}

// RemoteInfo はボードの親のカードに出すリモートとの差（`ccnavi --explain --json` の `remote`）。
// {"branch": 親のワークツリーが今いるブランチ, "ahead": 未送信のコミット数, "behind": 遅れ}。数は最後に
// 取ってきた origin との差で、読めなければ null。
func RemoteInfo(path string) *pyjson.Object {
	branch, _ := tree.BranchOf(path)
	ahead, behind, ok := divergence(path, branch)
	if !ok {
		return pyjson.NewObject("branch", branch, "ahead", nil, "behind", nil)
	}
	return pyjson.NewObject("branch", branch, "ahead", ahead, "behind", behind)
}

// Line は親 1 本を 1 行で言う。
func Line(row Row) string {
	mr, next, unsent := "なし", "なし", "なし"
	if row.mr != "" {
		mr = "!" + row.mr
	}
	if row.nextChild != "" {
		next = row.nextChild
	}
	if row.unsent != "" {
		unsent = row.unsent + "あり"
	}
	return "- 親 " + row.Ident + "  ブランチ " + cmp.Or(row.branch, noBranch) + "  MR " + mr +
		"  次の子 " + next + "  未送信 " + unsent
}

// Child は子のワークツリー 1 本の状態。
type Child struct {
	Ident string
	State ChildState
}

// ChildStates は親の子のうち、ワークツリーがあるものの状態（フェーズ・識別子の順）。
func ChildStates(root string, row Row, doing, review []*ticket.Ticket) []Child {
	all := append(slices.Clone(doing), review...)
	slices.SortStableFunc(all, byPhase)
	var out []Child
	seen := map[string]bool{}
	for _, t := range all {
		if t.Parent != row.Ident || seen[t.ID] {
			continue
		}
		seen[t.ID] = true
		path := tree.WorktreePath(root, t.ID)
		if !ospath.IsDir(path) {
			continue
		}
		branch, ok := tree.BranchOf(path)
		if !ok {
			branch = t.ID
		}
		out = append(out, Child{t.ID, childState(path, branch, row.path)})
	}
	return out
}

// childState は未コミットがあれば未コミットあり（作業が残っているので合流の有無より先に言う）。そうでなければ、
// 子のブランチの先頭が親のワークツリーの HEAD の祖先なら合流済み。読めなければ未合流（合流済みと言い切らない側）。
func childState(path, branch, parentPath string) ChildState {
	dirty := git.Status(path, git.Porcelain{}, git.Options{})
	if dirty.OK() && ospath.Strip(dirty.Out) != "" {
		return childDirty
	}
	merged := git.IsAncestor(parentPath, "refs/heads/"+branch, "HEAD",
		git.Options{Timeout: git.DefaultTimeout})
	if merged.OK() {
		return childMerged
	}
	return childUnmerged
}

// Parents はセッション開始で渡す、このPCで作業中の親の一覧。無ければ空。
func Parents(conf *config.Settings, root string) string {
	if !conf.TicketsEnabled() {
		return ""
	}
	raw := approval.ReadRaw(conf, root)
	doing, _ := approval.Scan(conf, root, false, raw)
	closed, _ := approval.Scan(conf, root, true, raw)
	review, _ := approval.ScanReview(conf, root, raw)
	proposals := approval.ReadProposals(conf, root, raw).Kept
	rows := Collect(conf, root, doing, review, closed, proposals)
	if len(rows) == 0 {
		return ""
	}
	lines := []string{"[ccnavi] このPCで作業中の親:"}
	for _, r := range rows {
		lines = append(lines, Line(r))
	}
	return strings.Join(lines, "\n")
}
