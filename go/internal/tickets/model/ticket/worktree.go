package ticket

import "ccnavi/internal/infra/pyjson"

// WorktreeState は承認した子のワークツリーの計画の状態。拡張は WorktreeCreate だけを実行する。
type WorktreeState string

const (
	// WorktreeCreate は親のワークツリーが在り、子のワークツリーはまだ無い。承認の後に作る。
	WorktreeCreate WorktreeState = "create"
	// WorktreeExists は子のワークツリーが既に在る。何もしない。
	WorktreeExists WorktreeState = "exists"
	// WorktreeNoParent は親のワークツリーが無い（親を先に用意する）。作れない。
	WorktreeNoParent WorktreeState = "no-parent"
	// WorktreeProject はプロジェクトの子。置き場の決まりが別なので、承認の後には作らない。
	WorktreeProject WorktreeState = "project"
)

// WorktreePlan は承認した子 1 つのワークツリーの計画（承認の JSON の worktrees の 1 件）。組むのは
// lifecycle/worktrees、承認の文に言うのは proposal/agree。Path はワークスペースルートからの相対（区切りは "/"）、
// Base は WorktreeCreate のときの元のブランチ。
type WorktreePlan struct {
	Ticket, Parent, Path, Branch, Base string
	State                              WorktreeState
}

// JSON は承認の JSON の worktrees に載せる形（キーの順も拡張との契約）。
func (p WorktreePlan) JSON() *pyjson.Object {
	return pyjson.NewObject("ticket", p.Ticket, "parent", p.Parent, "path", p.Path, "branch", p.Branch,
		"base", p.Base, "state", string(p.State))
}
