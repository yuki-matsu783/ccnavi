package stop

import (
	"fmt"
	"strconv"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/lifecycle/closing"
	"ccnavi/internal/tickets/model/standing"
	"ccnavi/internal/tickets/model/ticket"
)

// CodeFinishNudge は finish の打ち忘れを促すときの理由コード。
const CodeFinishNudge = "NUDGE_TICKET_FINISH"

// Unfinished は Stop で finish を促す相手。ahead は自分で作ったコミットの数。
type Unfinished struct {
	Ticket   *ticket.Ticket
	worktree string
	ahead    int
	head     string
}

// Key はチケットの識別子（ほかの促しのキーは頭を付けて分ける）。
func (u *Unfinished) Key() string { return u.Ticket.ID }

// Digest は HEAD。コミットを足せばまた促す。
func (u *Unfinished) Digest() string { return u.head }

// here は cwd のツリー（ワークスペースルートの外・ルート自身なら偽）。
func here(conf *config.Settings, root, cwd string) (tree.Tree, bool) {
	if cwd == "" {
		cwd, _ = ospath.Getwd()
	}
	t, ok := tree.TreeOf(root, cwd, conf.Projects)
	if !ok || t.IsMain() {
		return tree.Tree{}, false
	}
	return t, true
}

// treeLookup はワークツリーの名前で承認済みチケットを引く。信頼できない（blocked）ものは引けないとする。
func treeLookup(index map[string]*ticket.Ticket, name string) (*ticket.Ticket, bool) {
	t, ok := tree.Lookup(index, name)
	return t, ok && t != nil && t.Blocked == ""
}

// UnfinishedAtStop は cwd のワークツリーの承認済みチケットが、finish を打ち忘れていそうなら返す。作業中で
// 着手済み・blocked でない・基準点がある・親なら閉じられる（CloseProblems が空）・未コミットの変更が無い・
// 自分で作ったコミットが 1 件以上、を全部満たすときだけ。raw は CloseProblems と同じ。
func UnfinishedAtStop(conf *config.Settings, root, cwd string, raw *approval.Raw) *Unfinished {
	at, ok := here(conf, root, cwd)
	if !ok {
		return nil
	}
	copies, _ := approval.Scan(conf, root, false, raw)
	bound, ok := treeLookup(approval.ByID(copies), at.Name)
	if !ok || !bound.InProgress() || bound.BaseSHA == "" {
		return nil
	}
	if !bound.IsChild() && len(closing.CloseProblems(conf, root, bound.ID, raw)) > 0 {
		return nil
	}
	status := git.Status(at.Root, git.Porcelain{Untracked: git.UntrackedAll}, git.Options{})
	rc, out := status.Code, status.Out
	if rc != 0 || ospath.Strip(out) != "" {
		return nil
	}
	parentBranch := ""
	if bound.IsChild() {
		parentBranch = standing.NewFamilies(conf, root).Branch(bound.Parent, bound.Project)
	}
	ahead := ownCommits(at.Root, bound, parentBranch)
	head := git.Head(at.Root, git.Options{})
	if ahead <= 0 || head == "" {
		return nil
	}
	return &Unfinished{bound, at.Root, ahead, head}
}

// ownCommits は基準点より先の、このチケットが自分で作ったコミットの数（マージのコミットは除く）。数えられなければ 0。
//
// 子は親のブランチに無いものがそのまま自分の仕事で、基準点から数えない（親を取り込んでも、親にあるほかの子の
// コミットは除かれる）。親のブランチを引けなければ自分のものか決まらないので 0。親は基準点から数え、統合先
// （origin/HEAD）にあるものを除く。origin/HEAD が無ければ基準点の先を全部数える。
func ownCommits(worktree string, t *ticket.Ticket, parentBranch string) int {
	o := git.Options{}
	resolve := func(rev string) string {
		rc, sha := git.Output(worktree, []string{"rev-parse", "--verify", "--quiet", rev}, o)
		if rc != 0 {
			return ""
		}
		return ospath.Strip(sha)
	}
	args := []string{"rev-list", "--count", "--no-merges"}
	if t.IsChild() {
		if parentBranch == "" {
			return 0
		}
		sha := resolve(parentBranch + "^{commit}")
		if sha == "" {
			return 0
		}
		args = append(args, "HEAD", "^"+sha)
	} else {
		args = append(args, t.BaseSHA+"..HEAD")
		if sha := resolve("refs/remotes/origin/HEAD^{commit}"); sha != "" {
			args = append(args, "^"+sha)
		}
	}
	rc, out := git.Output(worktree, args, o)
	if rc != 0 {
		return 0
	}
	n, _ := git.Count(out)
	return n
}

// FinishNudge は Stop を止めて渡す文。文は文面ファイルが持つ。
func FinishNudge(root string, u *Unfinished) string {
	t := u.Ticket
	what := "parent"
	if t.IsChild() {
		what = "child"
	}
	said := texts.Say(root, "NUDGE_TICKET_FINISH", texts.Values{
		"ticket":         t.ID,
		"worktree":       u.worktree,
		"ahead":          strconv.Itoa(u.ahead),
		"base":           prefix(t.BaseSHA, git.ShortSHA),
		"user_stop_note": userStopNote,
	}, texts.Variants{"what": what})
	return hookio.Tagged(CodeFinishNudge, "ticket: "+t.ID) + "\n" + said
}

func prefix(s string, n int) string {
	r := []rune(s)
	if len(r) > n {
		return string(r[:n])
	}
	return s
}

// BaseOffHead は基準点（base_sha）がチケットのワークツリーの HEAD の祖先でないときの文。でなければ空。
// 手で動かした承認では提案の段階で書かれた基準点が入りうる（`--lint` と status が warn で言う）。
// ワークツリーが無い・HEAD を読めないときは確かめられないので言わない。
func BaseOffHead(conf *config.Settings, root string, t *ticket.Ticket) string {
	if t.BaseSHA == "" {
		return ""
	}
	owner := tree.ProjectRoot(conf.Projects, t.Project)
	if owner == "" {
		owner = root
	}
	worktree := tree.WorktreePath(root, t.ID)
	if !tree.IsWorktreeOf(owner, worktree) {
		return ""
	}
	head := git.Head(worktree, git.Options{})
	if head == "" || git.IsAncestor(worktree, t.BaseSHA, head, git.Options{}).OK() {
		return ""
	}
	return fmt.Sprintf("基準点（base_sha: %s）がワークツリー %s の HEAD の祖先でない。"+
		"着手の欄はスクリプトだけが書くもので、提案の段階で書かれた値か、ワークツリーの履歴を"+
		"書き換えたあとかもしれない。ユーザに承認済みチケットの base_sha を確かめてもらってください", t.BaseSHA, worktree)
}
