package catchup

import (
	"fmt"
	"io"
	"strings"

	"ccnavi/internal/entry/shask"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/reply"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/lifecycle/integrate"
	"ccnavi/internal/tickets/model/ids"
	"ccnavi/internal/tickets/model/standing"
)

// answer は sh への答え（`infra/reply` の形）。行の先頭の語は out・err・failed・overlap（取り込みが書きかけと重なって止まった）・noticket（渡された親のワークツリーに親チケットも提案も無い）・next・exit と、データの行（plan の repo・family など）。
// sync の終了コードは、1 回の起動の答えを並べて end が決める（exitOf）。
// 値はどれも 1 行に収める（reply.Lines.Line）。
type answer struct{ reply.Lines }

// out は標準出力に出す 1 行。
func (a *answer) out(format string, args ...any) { a.Line("out", fmt.Sprintf(format, args...)) }

// fail は標準出力に出す 1 行で、その親子のチケットかリポジトリを止めたものに数える（終了コード 1）。
func (a *answer) fail(format string, args ...any) {
	a.out(format, args...)
	a.Line("failed")
}

// next は次の一手（sh が打つ外との出入り）。
func (a *answer) next(words ...string) { a.Line("next", words...) }

// write は答えを書いて 0 を返す。
func (a *answer) write(w io.Writer) int {
	fmt.Fprint(w, a.String())
	return config.ExitOK
}

// ---- ローカルの git

// mergeOptions は早送りと merge の git の起こし方。断った文を英語で読み分ける（refusal.Of）ので LC_ALL=C にする。
var mergeOptions = git.Options{Timeout: git.MergeTimeout, CLocale: true}

// countOf は rev-list --count の数。読めなければ空。
func countOf(dir, revs string) string {
	text, ok := git.Text(dir, []string{"rev-list", "--count", revs}, git.Options{})
	if _, number := git.Count(text); !ok || !number {
		return ""
	}
	return text
}

// shortOf は文に出す短い sha。
func shortOf(dir, ref string) string {
	text, _ := git.Text(dir, []string{"rev-parse", "--short", ref}, git.Options{})
	return text
}

// fastForward は dir のチェックアウトを target まで早送りする。無視しているファイルは上書きしない
// （--no-overwrite-ignore）。断られたら git の標準エラーを返す。
func fastForward(dir, target string) (bool, string) {
	done := git.Run(dir, []string{"merge", "--ff-only", "--no-overwrite-ignore", "--quiet", target}, mergeOptions)
	return done.OK(), done.Stderr + done.Out
}

// hostDefault は `ls-remote --symref origin HEAD` の出力から、ホストの既定ブランチの名前を読む。読めない・
// 先頭が `-`（後ろの git の引数でオプションに読まれる）・ブランチ名の形でないときは空。
func hostDefault(dir, text string) string {
	for _, line := range ospath.Splitlines(text) {
		rest, ok := strings.CutPrefix(line, "ref: refs/heads/")
		if !ok {
			continue
		}
		name, tail, ok := strings.Cut(rest, "\t")
		if !ok || ospath.Strip(tail) != "HEAD" {
			continue
		}
		name = ospath.Strip(name)
		if name == "" || strings.HasPrefix(name, "-") ||
			!git.Run(dir, []string{"check-ref-format", "refs/heads/" + name}, git.Options{}).OK() {
			return ""
		}
		return name
	}
	return ""
}

// retarget は手元の origin/HEAD を origin/<名前> に向け直す。origin/<名前> が無ければ（取ってこられていなければ）
// 向け直さない（無い ref を指す状態を残さない）。向け直したら真。
func retarget(repo, name string) bool {
	if git.Commit(repo, "refs/remotes/origin/"+name, git.Options{}) == "" {
		return false
	}
	return git.Run(repo, []string{"symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/" + name}, git.Options{}).OK()
}

// treeOf は、そのブランチをチェックアウトしているツリーのパス（`worktree list --porcelain` の worktree の行）。
// どこにも無ければ空。チェックアウトされているブランチの ref は update-ref で書き換えない（索引とワークツリーが
// 食い違う）ので、在れば早送り、無ければ update-ref に分ける。
func treeOf(repo, branch string) string {
	list, _ := git.Worktrees(repo, git.Options{})
	w, _ := git.Holding(list, branch)
	return w.Path
}

// ---- 親のワークツリーの選び方

// family は親のワークツリーを選べなかった理由。
type family int

const (
	isFamily  family = iota
	noTree           // .claude/worktrees/<名前> がディレクトリでない（無い・リンク）
	detached         // HEAD がブランチを指していない
	mismatch         // HEAD のブランチ名を変換した識別子が名前と合わない・使えないブランチ名
	notParent        // 親チケットも提案も無い
	unknown          // 親かを確かめられない（置き場のファイルを読めない）
)

// familyAt は、`.claude/worktrees/<名前>` が親子のチケットの親のワークツリーかを見て、そのパスと親のブランチ名を返す。
// 親のワークツリーは、HEAD のブランチ名の `/` を `-` に置き換えた識別子がフォルダ名と同じで、親チケットか提案を
// 持つもの。他セッションのワークツリーを動かさないため、条件を全部満たすときだけ isFamily。ccnavi-sync.sh と
// ccnavi-fetch.sh が同じ選び方をする。
func familyAt(root string, conf *config.Settings, name string) (path, branch string, why family) {
	path = tree.WorktreePath(root, name)
	if !ospath.IsDir(path) || ospath.IsLink(path) {
		return path, "", noTree
	}
	branch, ok := tree.BranchOf(path)
	if !ok {
		return path, "", detached
	}
	if standing.HeadBranch(path, name) == "" || !ids.IsValidFamily(name) || branch != name && ids.BranchProblem(branch) != "" {
		return path, branch, mismatch
	}
	switch shask.IsParentTree(root, conf, path, name) {
	case 0:
		return path, branch, isFamily
	case 1:
		return path, branch, notParent
	}
	return path, branch, unknown
}

// familyKey は親のワークツリーの元のリポジトリの名前（ロックの置き場の名前。ワークスペース自身は self）。
func familyKey(root string, conf *config.Settings, path, name string) string {
	return tree.RepoKey(integrate.TreeAt(conf, root, path, name).Project)
}

// worktreeNames は `.claude/worktrees/` の直下の名前（並びはディスクのとおり）。
func worktreeNames(root string) []string {
	names, _ := ospath.DiskListdir(tree.WorktreesPath(root))
	return names
}
