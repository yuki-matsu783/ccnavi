package shellguard

import (
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/approval"
)

// 理由コードと、記録の rules に残す組み込みのルールの名前。
const (
	CodeEnv          = "DENY_SCRIPT_ENV_OVERRIDE"
	CodeChildPush    = "DENY_CHILD_PUSH"
	CodeCorePath     = "DENY_WRAPPER_CORE_PATH"
	CodeParentTree   = "DENY_SUBAGENT_PARENT_TREE"
	EnvRuleID        = rules.ReservedIDPrefix + "script-env"
	ChildPushRuleID  = rules.ReservedIDPrefix + "child-push"
	CorePathRuleID   = rules.ReservedIDPrefix + "guard-wrapper-paths"
	ParentTreeRuleID = rules.ReservedIDPrefix + "subagent-parent-tree"
)

// Role はワークツリーの役。チケットが無ければ空（直接作業のワークツリーなど）。
type Role string

const (
	roleChild  Role = "child"
	RoleParent Role = "parent"
)

// unknownParent は親を決められない（読めない・追えない）ときに名前の代わりに出す印。呼び手は止める側に倒す。
const unknownParent = approval.UnknownParent

// worktreeName は place がワークスペースの `.claude/worktrees/<名前>` の中なら、在るディレクトリの綴りの名前
// （呼び手がパスを組み直して開く）。git に登録されているかは問わない（sh の検査と同じ広さ）。
func worktreeName(root, where string) string {
	rest, ok := tree.PartsUnder(tree.WorktreesPath(root), where)
	if !ok || len(rest) == 0 {
		return ""
	}
	return ospath.Basename(tree.WorktreePath(root, rest[0]))
}

// TicketRole はその名前のワークツリーの役と、子なら親の識別子。
//
// 見るのはそのワークツリー自身のツリーの同じ名前のチケットだけ。ほかのツリーに置いたファイルで役を決めると、
// 子のワークツリーに `parent:` を書いた偽のチケットを 1 つ置くだけで、親のワークツリーの禁止が丸ごと外れてしまう。
// 見つかったものすべてに同じ `parent:` があれば子とする。`parent:` が無いもの・読めないもの・親の食い違いがあれば、
// 親とみなして止める側に倒す。
func TicketRole(conf *config.Settings, root, name string) (Role, string) {
	top := ospath.Join(root, tree.WorktreesDir, name)
	var parents []string
	for _, path := range approval.TicketPaths(conf, top, name) {
		if !ospath.DiskLexists(path) {
			continue
		}
		parent, _, ok := approval.ParentOfFile(path)
		if !ok || parent == "" {
			return RoleParent, ""
		}
		parents = append(parents, parent)
	}
	if len(parents) == 0 {
		return "", ""
	}
	for _, p := range parents[1:] {
		if p != parents[0] {
			return RoleParent, ""
		}
	}
	return roleChild, parents[0]
}

// isParentTree はその名前のワークツリーが親のチケットのものか（TicketRole が RoleParent）。
func isParentTree(conf *config.Settings, root, name string) bool {
	role, _ := TicketRole(conf, root, name)
	return role == RoleParent
}
