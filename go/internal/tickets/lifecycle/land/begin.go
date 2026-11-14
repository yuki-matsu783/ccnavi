package land

import (
	"strconv"
	"strings"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/lifecycle/integrate"
	"ccnavi/internal/tickets/model/ids"
)

// readRecord は記録を読む（同じキーが 2 行あれば最初の行）。
var readRecord = integrate.ReadRecord

// checkName は取り込むワークツリーの名前を確かめる。
func (r *run) checkName(name string) {
	if name == "" {
		r.reject("integrate-no-name", r.sub+" には取り込むワークツリーの名前を渡してください（"+r.Self+" "+r.sub+" <名前>。.claude/worktrees/<名前> の名前）。")
	}
	if !ids.IsValidFamily(name) {
		r.reject("integrate-name", r.sub+" の名前（"+name+"）は .claude/worktrees/ の直下の名前の形ではありません。パスではなく名前だけを渡してください（"+r.Self+" "+r.sub+" <名前>）。")
	}
	switch {
	case name == "main" || name == "master" || name == "develop" || name == "release" || strings.HasPrefix(name, "release-"):
		r.reject("integrate-name", name+" は統合先に使う名前です。取り込むのは .claude/worktrees/<名前> の作業用のブランチです（"+r.Self+" "+r.sub+" <名前>）。")
	}
}

// gitPath は `rev-parse --git-path <名前>` の絶対パス。
func (r *run) gitPath(name string) string {
	p := r.out("rev-parse", "--git-path", name)
	if p != "" && !ospath.IsAbs(p) {
		p = ospath.Join(r.Cwd, p)
	}
	return p
}

// notRepo は git リポジトリの外で打ったときの拒否。
func (r *run) notRepo() {
	r.reject("not-a-repo", "git リポジトリの中で実行してください（取り込み先のブランチをチェックアウトしているユーザのチェックアウトで "+r.SelfQ+" "+r.sub+" ... を実行します）。")
}

// probe は記録の置き場だけを調べ、記録の版（"4"・"none"・それ以外は知らない版）を返す。
func (r *run) probe(name string) string {
	r.checkName(name)
	r.name = name
	if r.out("rev-parse", "--show-toplevel") == "" {
		r.notRepo()
	}
	r.gdir = r.gitPath(integrate.RecordDir)
	if r.gdir == "" {
		r.reject("integrate-no-gitdir", "git ディレクトリの場所が読めません。ユーザに伝えてください。")
	}
	r.mark = ospath.Join(r.gdir, name, "marker")
	if !ospath.IsFile(r.mark) {
		return "none"
	}
	if v := record(r.mark)["version"]; v != "" {
		return v
	}
	return "無し"
}

// knownVersion は、記録の版が 4 か、記録が無いことを確かめる。
func (r *run) knownVersion(version string) {
	if version != "4" && version != "none" {
		r.reject("integrate-marker", "取り込みの記録（"+ospath.Join(r.gdir, r.name)+"/marker）の版（"+version+"）を知りません。版 4 でない記録は ccnavi が読めません。ユーザが git status で統合先の staged を確かめ、不要なら git reset --merge で戻してから、"+ospath.Join(r.gdir, r.name)+" を手で消してください。エージェントは操作しません。")
	}
}

// userCheckout は git worktree list の先頭（ユーザのチェックアウト）。
func (r *run) userCheckout() string {
	first, _, _ := strings.Cut(r.out("worktree", "list", "--porcelain"), "\n")
	return strings.TrimPrefix(first, "worktree ")
}

// begin は、名前・場所・記録の置き場を決める。ユーザのチェックアウト（git worktree list の先頭）で打たれていることを求める。
func (r *run) begin() {
	r.top = r.out("rev-parse", "--show-toplevel")
	if r.top == "" {
		r.notRepo()
	}
	r.common = r.out("rev-parse", "--path-format=absolute", "--git-common-dir")
	if r.common == "" {
		r.reject("integrate-no-gitdir", "git の共通ディレクトリの場所が読めません。ユーザに伝えてください。")
	}
	r.common = ospath.Realpath(r.common)
	r.main = r.userCheckout()
	if r.main == "" || !pathcase.SameReal(r.top, r.main) {
		r.reject("integrate-not-main", r.sub+" はユーザのチェックアウト（"+orNone(r.main, "見つかりません")+"）でだけ実行します。ここ（"+r.top+"）は違います。cd してから、同じコマンドを再実行してください。")
	}
	r.wt = tree.WorktreePath(r.Root, r.name)
	here, _ := tree.TreeOf(r.Root, ospath.Abspath(r.top), integrate.ProjectsDir(r.Conf, r.Root))
	r.repo = tree.RepoKey(here.Project)
	r.rec = ospath.Join(r.common, tree.DirectDir, r.name)
	r.doneFile = ospath.Join(r.gdir+"-done", r.name)
	r.ref = "refs/ccnavi/direct/" + r.name
	r.tmp = ospath.Join(r.Root, "logs", "direct-tmp", r.repo, r.name)
	r.head = r.out("symbolic-ref", "--quiet", "--short", "HEAD")
}

// orNone は s が空なら none。
func orNone(s, none string) string {
	if s == "" {
		return none
	}
	return s
}

// commonCheck は、実行した場所・ユーザのチェックアウト・作業ワークツリー・一時ワークツリーの git の共通ディレクトリが、
// 記録の common_dir と同じ実パスであることを確かめる（あるものだけ）。
func (r *run) commonCheck() {
	want := record(r.rec)["common_dir"]
	if want == "" {
		want = record(r.doneFile)["common_dir"]
	}
	if want == "" {
		return
	}
	for _, dir := range []string{r.top, r.main, r.wt, r.tmp} {
		if !ospath.IsDir(dir) {
			continue
		}
		got, _ := r.git(dir, "rev-parse", "--path-format=absolute", "--git-common-dir")
		if got == "" {
			continue
		}
		if !pathcase.SameReal(got, want) {
			r.reject("integrate-common-dir", dir+" の git の共通ディレクトリ（"+ospath.Realpath(got)+"）が、記録の common_dir（"+ospath.Realpath(want)+"）と違います。別のリポジトリの記録かもしれないので、何も変えずに止めます。ユーザに確かめてください。")
		}
	}
}

// gitAtLeast は git が major.minor 以上か。
func (r *run) gitAtLeast(major, minor int) bool {
	v := strings.TrimPrefix(r.out("version"), "git version ")
	parts := strings.SplitN(v, ".", 3)
	if len(parts) < 2 {
		return false
	}
	maj, err1 := strconv.Atoi(parts[0])
	min, err2 := strconv.Atoi(strings.TrimRightFunc(parts[1], func(c rune) bool { return c < '0' || c > '9' }))
	if err1 != nil || err2 != nil {
		return false
	}
	return maj > major || maj == major && min >= minor
}

// onBase は、ユーザのチェックアウトが取り込み先を開いていることを確かめる。
func (r *run) onBase(base string) {
	if r.head != base {
		r.reject("integrate-not-base", "ユーザのチェックアウトが取り込み先（"+base+"）を開いていません（今は "+orNone(r.head, "ブランチの外（detached HEAD）")+"）。ブランチの切り替えはユーザに戻してもらってください（エージェントは checkout しない）。戻ったら同じコマンドを再実行します。")
	}
}

// originCount は、手元の refs/remotes/origin/<取り込み先> が無ければ -1、あれば範囲 rng のコミットの数。
func (r *run) originCount(base, rng string) int {
	if r.verify("refs/remotes/origin/"+base+"^{commit}") == "" {
		return -1
	}
	n, err := strconv.Atoi(strings.TrimSpace(r.out("rev-list", "--count", rng)))
	if err != nil {
		return -1
	}
	return n
}

// bound は作業ワークツリーがチケットに結び付くか（yes・no・unavailable）と理由。`--adopt` と同じ判定。
func (r *run) bound() (string, string) {
	if !ospath.IsDir(r.wt) {
		return "unavailable", ""
	}
	if why := integrate.BoundReason(r.Conf, r.Root, integrate.TreeAt(r.Conf, r.Root, r.wt, r.name)); why != "" {
		return "yes", why
	}
	return "no", ""
}

// worktreeBranch は .claude/worktrees/<名前> がこのリポジトリのワークツリーなら、チェックアウトしている ref。
func (r *run) worktreeBranch() string {
	path := ""
	for _, line := range strings.Split(r.out("worktree", "list", "--porcelain"), "\n") {
		switch {
		case strings.HasPrefix(line, "worktree "):
			path = strings.TrimPrefix(line, "worktree ")
		case strings.HasPrefix(line, "branch ") && path != "" && pathcase.SameReal(path, r.wt):
			return strings.TrimPrefix(line, "branch ")
		}
	}
	return ""
}

// dirty は dir の `status --porcelain` の先頭 10 行（空白でつなぐ）。変更が無ければ空。
func (r *run) dirty(dir string) string {
	out, _ := r.git(dir, "status", "--porcelain")
	lines := ospath.Splitlines(out)
	if len(lines) > 10 {
		lines = lines[:10]
	}
	return strings.Join(lines, " ")
}

// dropMark は記録を消す。消せなければ偽。
func (r *run) dropMark() bool {
	removeQuiet(r.mark)
	removeDirQuiet(ospath.Join(r.gdir, r.name))
	removeDirQuiet(r.gdir)
	return !exists(r.mark)
}
