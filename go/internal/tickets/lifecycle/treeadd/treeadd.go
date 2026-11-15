// Package treeadd は、ワークツリーを `.claude/worktrees/<名前>` に出す共通の口（`git worktree add`）。
// 新しい親（ccnavi-start.sh の start）・直接作業（ccnavi-worktree.sh の cut）・子の着手（ccnavi-ticket.sh の ticketsh）が使う。
// 実行ファイルが打つローカルの git の書き込みなので、ラッパー（ccnavi-git.sh）が通すかを決めるのと同じ検査（gitwrap.Judge）を
// 通してから打つ。
package treeadd

import (
	"strings"
	"time"

	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/tickets/lifecycle/gitwrap"
)

// timeout は worktree add の期限。checkout のフックを待つので長くとる。
const timeout = 10 * time.Minute

// Add は cwd のリポジトリで、dest にワークツリーを出す。from が空なら既にあるブランチ branch を出し、空でなければ
// from から branch を新しく切る（追跡は付けない）。出せなければ、ラッパーが断った文か git の出力を返す。
func Add(root, cwd, dest, branch, from string) (said string, ok bool) {
	args := []string{"worktree", "add", dest, branch}
	if from != "" {
		args = []string{"worktree", "add", dest, "-b", branch, "--no-track", from}
	}
	if a := gitwrap.Judge(env(root, cwd), args); a.Deny != nil {
		return a.Deny.Message, false
	}
	done := git.Run(cwd, args, git.Options{Timeout: timeout, LazyFetch: true})
	if !done.OK() {
		text := strings.TrimRight(done.Stderr+done.Out, "\n")
		if text == "" && done.Failure != nil {
			text = done.Failure.Error()
		}
		return text, false
	}
	return "", true
}

// env は、ラッパーの判定の材料。呼び方はワークスペースの `.ccnavi/scripts/` の sh で書く（ラッパーが自分を呼ぶ形と同じ）。
// worktree add の判定は親のワークツリーかを見ないので、ParentTree は「決められない」と答える。
func env(root, cwd string) *gitwrap.Env {
	scripts := ospath.Join(root, ".ccnavi", "scripts")
	sh := func(name string) string { return ospath.Join(scripts, name) }
	return &gitwrap.Env{
		Names: gitwrap.Names{
			Self: "sh " + sh("ccnavi-git.sh"), Sync: "sh " + sh("ccnavi-sync.sh"), Start: "sh " + sh("ccnavi-start.sh"),
			SelfQ: "sh " + shell.SingleQuote(sh("ccnavi-git.sh")), WorktreeQ: "sh " + shell.SingleQuote(sh("ccnavi-worktree.sh")),
		},
		Root: root, Cwd: cwd,
		ParentTree: func(string, string) int { return 2 },
	}
}
