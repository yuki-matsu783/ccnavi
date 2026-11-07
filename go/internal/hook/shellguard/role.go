package shellguard

import (
	"regexp"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/model/place"
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
	RoleChild  Role = "child"
	RoleParent Role = "parent"
)

// unknownParent は親を決められない（読めない・追えない）ときに名前の代わりに出す印。呼び手は止める側に倒す。
const unknownParent = "?"

// parentLine はチケットの `parent:` の行。YAML として読まず、行で見る（sh の検査と同じ広さ。読めない
// frontmatter でも `parent:` の行があれば子と見る）。
var parentLine = regexp.MustCompile(`(?m)^parent:[ \t]*(.*?)[ \t]*$`)

// WorktreeName は place がワークスペースの `.claude/worktrees/<名前>` の中なら、在るディレクトリの綴りの名前
// （呼び手がパスを組み直して開く）。git に登録されているかは問わない（sh の検査と同じ広さ）。
func WorktreeName(root, where string) string {
	rest, ok := tree.PartsUnder(tree.WorktreesPath(root), where)
	if !ok || len(rest) == 0 {
		return ""
	}
	return ospath.Basename(tree.WorktreePath(root, rest[0]))
}

// ticketFiles はツリー top の、名前 name のチケットの置き場（doing・done・review）。
func ticketFiles(conf *config.Settings, top, name string) []string {
	approved := conf.ApprovedDir(top)
	proposals := ospath.Join(top, strings.ReplaceAll(conf.Tickets, "/", ospath.Sep))
	file := name + ".md"
	return []string{
		ospath.Join(approved, string(place.Doing), file),
		ospath.Join(approved, string(place.Done), file),
		ospath.Join(proposals, string(place.Review), file),
	}
}

func parentOf(text string) (string, bool) {
	m := parentLine.FindStringSubmatch(text)
	if m == nil {
		return "", false
	}
	return strings.Trim(m[1], `'"`), true
}

// TicketRole はその名前のワークツリーの役と、子なら親の識別子。
//
// 見るのはそのワークツリー自身のツリーの同じ名前のチケットだけ。ほかのツリーに置いたファイルで役を決めると、
// 子のワークツリーに `parent:` を持つ偽のチケットを 1 つ置くだけで、親のワークツリーの禁止が丸ごと外れてしまう。
// 見つかったものすべてに同じ `parent:` があれば子とする。`parent:` が無いもの・読めないもの・親の食い違いがあれば、
// 親とみなして止める側に倒す。
func TicketRole(conf *config.Settings, root, name string) (Role, string) {
	top := ospath.Join(root, tree.WorktreesDir, name)
	var parents []string
	for _, path := range ticketFiles(conf, top, name) {
		if !ospath.DiskLexists(path) {
			continue
		}
		text, err := fsio.ReadDiskText(path, fsio.Replace)
		if err != nil {
			return RoleParent, ""
		}
		parent, _ := parentOf(text)
		if parent == "" {
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
	return RoleChild, parents[0]
}

// IsParentTree はその名前のワークツリーが親のチケットのものか（TicketRole が RoleParent）。
func IsParentTree(conf *config.Settings, root, name string) bool {
	role, _ := TicketRole(conf, root, name)
	return role == RoleParent
}

// childParent は、その名前の承認済みチケットが子なら親。子でなければ空。
//
// 探すのは、ワークスペースルート・プロジェクトの置き場の下・`.claude/worktrees/` の下のディレクトリ全部。git の
// リポジトリかどうかは問わない。読めないファイルは「子かもしれない」として止める側にする（親は `?`）。
func childParent(conf *config.Settings, root, name string) string {
	for _, top := range searchedTrees(conf, root) {
		for _, path := range ticketFiles(conf, top, name) {
			if !ospath.IsFile(path) {
				continue
			}
			text, err := fsio.ReadDiskText(path, fsio.Replace)
			if err != nil {
				return unknownParent
			}
			if parent, ok := parentOf(text); ok {
				if parent == "" {
					return unknownParent
				}
				return parent
			}
		}
	}
	return ""
}

func searchedTrees(conf *config.Settings, root string) []string {
	tops := []string{root}
	for _, base := range []string{conf.Projects, tree.WorktreesPath(root)} {
		if base == "" {
			continue
		}
		names, err := ospath.DiskListdir(base)
		if err != nil {
			continue
		}
		for _, n := range names {
			if ospath.IsDir(ospath.Join(base, n)) {
				tops = append(tops, ospath.Join(base, n))
			}
		}
	}
	return tops
}
