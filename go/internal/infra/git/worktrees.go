package git

import (
	"strings"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
)

// worktreeArgs は、ワークツリーの一覧を機械向けの形で出す git の引数。
var worktreeArgs = []string{"worktree", "list", "--porcelain"}

// Worktree は `git worktree list --porcelain` の 1 つのツリー。
type Worktree struct {
	// Path は worktree の行のパス（git の綴りのまま）。
	Path string
	// Ref は branch の行の ref（refs/heads/<名前>）。detached・bare なら空。
	Ref string
}

// Branch は Ref から refs/heads/ を外したブランチ名。ブランチを開いていなければ空。
func (w Worktree) Branch() string { return strings.TrimPrefix(w.Ref, refsHeads) }

// parseWorktrees は `git worktree list --porcelain` の出力を読む。並びは git のとおりで、先頭がユーザのチェックアウト。
func parseWorktrees(out string) []Worktree {
	var list []Worktree
	for _, line := range ospath.Splitlines(out) {
		if path, ok := strings.CutPrefix(line, "worktree "); ok {
			list = append(list, Worktree{Path: path})
		} else if ref, ok := strings.CutPrefix(ospath.Strip(line), "branch "); ok && len(list) > 0 {
			list[len(list)-1].Ref = ref
		}
	}
	return list
}

// Worktrees は cwd のリポジトリのワークツリーの一覧。git が読めなければ偽。
func Worktrees(cwd string, o Options) ([]Worktree, bool) {
	done := Run(cwd, worktreeArgs, o)
	if !done.OK() {
		return nil, false
	}
	return parseWorktrees(done.Out), true
}

// Holding は、list のうちブランチ branch を開いているツリー。名前は大文字小文字と NFC を区別せずに比べる
// （pathcase.SameName）。無ければ偽。
func Holding(list []Worktree, branch string) (Worktree, bool) {
	for _, w := range list {
		if w.Ref != "" && pathcase.SameName(w.Branch(), branch) {
			return w, true
		}
	}
	return Worktree{}, false
}

// Commit は rev が指すコミットの sha。無ければ（コミットでなければ）空。
func Commit(cwd, rev string, o Options) string {
	done := Run(cwd, []string{"rev-parse", "--verify", "--quiet", rev + "^{commit}"}, o)
	if !done.OK() {
		return ""
	}
	return ospath.Strip(done.Out)
}
