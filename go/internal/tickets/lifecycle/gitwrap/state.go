package gitwrap

import (
	"strings"
	"time"

	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/tickets/lifecycle/integrate"
)

// Env は、git の状態とワークスペースを読む判定の材料。git はラッパーの cwd（Cwd）で起動する。
type Env struct {
	Names
	// Root はワークスペースルート、Cwd はラッパーが打たれた場所。
	Root, Cwd string
	// ParentTree は、ツリー top（名前 name）が親チケットのワークツリーか。0 は親、1 は親でない、2 は決められない。
	ParentTree func(top, name string) int

	top, prefix *string
}

// Answer は Judge の答え。Deny が nil でなければ拒否。
type Answer struct {
	// Verdict は Allow か Pass（続きの判定をまだラッパーが持つ）。
	Verdict string
	Deny    *Deny
	// Prepend は git の引数の前に足す語（ワークツリーの中の merge の --no-ff）。
	Prepend []string
	// Warn は、通したうえで出力の後ろに出す警告。
	Warn string
	// GateTop・GateBranch は、push の前にラッパーが確かめる、リモートに親のブランチが在るか（ls-remote）の対象。
	GateTop, GateBranch string
	// Target は、統合先をチェックアウトしているユーザのチェックアウトなら、その統合先の名前（pull が衝突したときの案内に使う）。
	Target string
}

// git は cwd で git を実行し、標準出力の末尾の改行を落として返す。
func (e *Env) git(args ...string) (string, bool) {
	done := git.Run(e.Cwd, args, git.Options{Timeout: git.MergeTimeout})
	return strings.TrimRight(done.Out, "\r\n"), done.OK()
}

// gitOK は git の終了コードが 0 だったか。
func (e *Env) gitOK(args ...string) bool {
	_, ok := e.git(args...)
	return ok
}

// Top は git のトップ（`rev-parse --show-toplevel`）。リポジトリの外なら空。
func (e *Env) Top() string {
	if e.top == nil {
		out, _ := e.git("rev-parse", "--show-toplevel")
		e.top = &out
	}
	return *e.top
}

// Prefix は cwd の git のトップからの相対（`rev-parse --show-prefix`）。
func (e *Env) Prefix() string {
	if e.prefix == nil {
		out, _ := e.git("rev-parse", "--show-prefix")
		e.prefix = &out
	}
	return *e.prefix
}

// branchNow は今のブランチ（`symbolic-ref --short HEAD`）。ブランチの外なら空。
func (e *Env) branchNow() string {
	out, _ := e.git("symbolic-ref", "--quiet", "--short", "HEAD")
	return out
}

// config は git の設定の値。無ければ空と偽。
func (e *Env) config(key string) (string, bool) {
	return e.git("config", "--get", key)
}

// hasRef は ref が在るか。
func (e *Env) hasRef(ref string) bool {
	return e.gitOK("show-ref", "--verify", "--quiet", ref)
}

// phys はリンクを解いたパス（`/` 区切り）。
func phys(p string) string {
	if p == "" {
		return ""
	}
	return ospath.Slashed(ospath.Realpath(p))
}

// worktreeName は、top がワークスペースの `.claude/worktrees/<名前>` の下なら、その名前。
func (e *Env) worktreeName(top string) (string, bool) {
	if top == "" {
		return "", false
	}
	rest, ok := pathcase.Under(phys(top), ospath.Join(phys(e.Root), ".claude", "worktrees"))
	if !ok || rest == "." {
		return "", false
	}
	name, _, _ := strings.Cut(rest, ospath.Sep)
	return name, true
}

// userCheckout は、top がユーザのチェックアウト（`git worktree list` の先頭）か。先頭の場所も返す。
func (e *Env) userCheckout(top string) (string, bool) {
	out, _ := e.git("worktree", "list", "--porcelain")
	first, _, _ := strings.Cut(out, "\n")
	main, ok := strings.CutPrefix(first, "worktree ")
	return main, ok && main != "" && pathcase.SameReal(top, main)
}

// integration は top が属するリポジトリの統合先の名前。決まらなければ空。
func integration(top string) string {
	name, _ := integrate.Integration(top, time.Time{})
	return name
}

// targetHere は、今のチェックアウトが統合先をチェックアウトしているユーザのものなら、統合先の名前。違えば空。
// 統合先が決まらない（origin が無いなど）ときは、どれが統合先か言えないので当てはまらない。
func (e *Env) targetHere() string {
	top := e.Top()
	if top == "" {
		return ""
	}
	if _, ok := e.userCheckout(top); !ok {
		return ""
	}
	if integ := integration(top); integ != "" && e.branchNow() == integ {
		return integ
	}
	return ""
}

// shQuote は単引用符で囲む（中の ' は '"'"'）。案内の文に、そのまま打てる語を載せる。
func shQuote(s string) string {
	return "'" + strings.ReplaceAll(s, "'", `'"'"'`) + "'"
}
