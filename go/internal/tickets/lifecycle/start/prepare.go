package start

import (
	"strings"
	"time"

	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/model/ids"
)

// PrepareArgs は Prepare の材料。
type PrepareArgs struct {
	Branch string
	// From は新しく切るときの起点（origin/<統合先>）。空なら既にあるブランチを出す。
	From string
	// FastForward は、出したワークツリーを origin/<ブランチ> に早送りするか。Initial は、統合先より進んでいなければ空の
	// 初期コミットを積むか。
	FastForward, Initial bool
	Integration, Number  string
	// Real は打ち直しの引数（作成者が無いときの案内に使う）。
	Real string
}

// prepareTimeout は、ワークツリーを作る・コミットするなどの 1 回の git の期限。フックを待つので長くとる。
const prepareTimeout = 10 * time.Minute

// Prepare は、ブランチのワークツリーを出し（無ければ .claude/worktrees/<識別子> に作る）、求められれば早送りと空の初期
// コミットを済ませる。どれも出来ていれば飛ばす。初期コミットを積めなければ、この回で作ったワークツリーとブランチを片付けて止める。
// 答えは `wt <パス>`、作ったときは `created yes`、早送りの結果は `ff <文>`。
func Prepare(root, cwd string, a PrepareArgs) (string, int) {
	var o outcome
	p := preparer{root: root, cwd: cwd, a: a, o: &o}
	if p.worktree() && (!a.FastForward || p.fastForward()) && (!a.Initial || p.initial()) {
		o.val("wt", p.wt)
		if p.created {
			o.val("created", "yes")
		}
		o.val("ff", p.ff)
	}
	return o.String(), 0
}

type preparer struct {
	root, cwd, wt, ff string
	created           bool
	a                 PrepareArgs
	o                 *outcome
}

// run は dir で git を起こす。失敗したら git の出力を標準エラーに出す行として残して偽。
func (p *preparer) run(dir string, args ...string) bool {
	done := git.Run(dir, args, git.Options{Timeout: prepareTimeout, LazyFetch: true})
	if !done.OK() {
		p.o.err(strings.TrimRight(done.Stderr+done.Out, "\n"))
	}
	return done.OK()
}

// worktree はブランチのワークツリーを出す。行き先は .claude/worktrees/<識別子>（ブランチ名の / を - に置き換えたもの）。
func (p *preparer) worktree() bool {
	r := repo{p.cwd}
	if p.wt = r.worktreeOf(p.a.Branch); p.wt != "" {
		return true
	}
	dest := tree.WorktreePath(p.root, ids.BranchToID(p.a.Branch))
	if ospath.DiskLexists(dest) {
		cur := strings.TrimRight(gitOut(dest, "rev-parse", "--abbrev-ref", "HEAD"), "\n")
		if cur == "" {
			cur = "?"
		}
		p.o.fail("worktree-busy", exitUnmet, "ワークツリーの作成先 "+dest+" が既にあり、ブランチ "+p.a.Branch+" のものではありません（現在のブランチ："+cur+"）。中を確かめ、不要なら片付けてから再実行してください。")
		return false
	}
	args := []string{"worktree", "add", dest, p.a.Branch}
	if p.a.From != "" {
		args = []string{"worktree", "add", dest, "-b", p.a.Branch, "--no-track", p.a.From}
	} else if r.branchRef(p.a.Branch) == "" {
		p.o.fail("branch-missing", exitUnmet, "ブランチ "+p.a.Branch+" が手元にも origin にもありません。ホストにだけある MR の元ブランチ（消された後など）かもしれません。")
		return false
	}
	if !p.run(p.cwd, args...) {
		p.o.fail("worktree-failed", exitUnmet, "ワークツリーを作成できませんでした（上の出力を確認してください）。")
		return false
	}
	p.wt, p.created = dest, p.a.From != ""
	return true
}

// fastForward はワークツリーの先頭を origin/<ブランチ> に早送りする。手元が先・分かれているときは動かさず言う
// （merge は ccnavi-sync.sh の仕事）。
func (p *preparer) fastForward() bool {
	remote := "refs/remotes/origin/" + p.a.Branch
	w := repo{p.wt}
	if !w.refExists(remote) {
		return true
	}
	local := strings.TrimRight(gitOut(p.wt, "rev-parse", "HEAD"), "\n")
	theirs := strings.TrimRight(gitOut(p.wt, "rev-parse", remote), "\n")
	if local == "" || theirs == "" || local == theirs {
		return true
	}
	origin := "origin/" + p.a.Branch
	switch {
	case git.Run(p.wt, []string{"merge-base", "--is-ancestor", local, theirs}, git.Options{}).OK():
		if p.run(p.wt, "merge", "--ff-only", "--no-overwrite-ignore", origin) {
			p.ff = origin + " に早送りした"
		} else {
			p.ff = origin + " に早送りできなかった（上の出力を見てください）"
		}
	case git.Run(p.wt, []string{"merge-base", "--is-ancestor", theirs, local}, git.Options{}).OK():
		p.ff = "手元が " + origin + " より先に進んでいる（未送信）"
	default:
		p.ff = "手元と " + origin + " が分かれている（取り込みは ccnavi-sync.sh が行う）"
	}
	return true
}

// initial は、統合先より進んでいなければ空の初期コミットを積む。積む前に作成者（author と committer）を確かめ、無ければ
// この回で作った分を片付けたうえで、設定を頼んで止める。
func (p *preparer) initial() bool {
	if n, ok := git.Count(gitOut(p.wt, "rev-list", "--count", "origin/"+p.a.Integration+"..HEAD")); ok && n >= 1 {
		return true
	}
	if !git.Run(p.wt, []string{"var", "GIT_AUTHOR_IDENT"}, git.Options{}).OK() ||
		!git.Run(p.wt, []string{"var", "GIT_COMMITTER_IDENT"}, git.Options{}).OK() {
		p.cleanup()
		p.o.out(texts.Say(p.root, "NEXT_START_IDENT", texts.Values{"real_args_q": p.a.Real}, nil))
		p.o.fail("ident-missing", exitUnmet, "git の作成者情報（user.name と user.email）が無いため、初期コミットを積めません。")
		return false
	}
	if !p.run(p.wt, "commit", "--allow-empty", "-m", InitSubject(p.a.Number)) {
		p.cleanup()
		p.o.fail("commit-failed", exitUnmet, "初期コミットを積めなかった（上の出力を見てください）。")
		return false
	}
	return true
}

// cleanup は、この回で新しく作ったワークツリーとブランチを片付ける。強制はしない（worktree remove に --force を
// 付けず、ブランチは -d で消す）。消せなければ、残ったことと続け方を言う。
func (p *preparer) cleanup() {
	if !p.created {
		return
	}
	if !git.Run(p.cwd, []string{"worktree", "remove", p.wt}, git.Options{Timeout: prepareTimeout, LazyFetch: true}).OK() {
		p.o.out(texts.Say(p.root, "NOTE_START_WORKTREE_KEPT", texts.Values{"wt": p.wt, "branch": p.a.Branch}, nil))
		return
	}
	if !git.Run(p.cwd, []string{"branch", "-d", p.a.Branch}, git.Options{}).OK() {
		p.o.out(texts.Say(p.root, "NOTE_START_BRANCH_KEPT", texts.Values{"branch": p.a.Branch}, nil))
	}
}
