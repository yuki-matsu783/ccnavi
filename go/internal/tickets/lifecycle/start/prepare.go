package start

import (
	"strings"
	"time"

	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/lifecycle/refusal"
	"ccnavi/internal/tickets/lifecycle/treeadd"
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
// 統合先から切るとき（From）は、sh が取ってきた origin/<統合先> が手元に無ければ止める（no-origin-integration）。
// 答えは `wt <パス>`、作ったときは `created yes`、早送りの結果は `ff <文>`、push の要否は `push needed|skip`
// （ワークツリーの先頭が手元の origin/<ブランチ> と同じなら skip。sh は skip なら push を打たない）。
func Prepare(root, cwd string, a PrepareArgs) (string, int) {
	if a.From != "" && !(repo{cwd}).refExists("refs/remotes/origin/"+a.Integration) {
		out, _ := Stop(root, "no-origin-integration", map[string]string{"integration": a.Integration})
		return out, 0
	}
	var o outcome
	p := preparer{root: root, cwd: cwd, a: a, o: &o}
	if p.worktree() && (!a.FastForward || p.fastForward()) && (!a.Initial || p.initial()) {
		o.Val("wt", p.wt)
		if p.created {
			o.Val("created", "yes")
		}
		o.Val("ff", p.ff)
		o.Val("push", p.pushPlan())
	}
	return o.String(), 0
}

// pushPlan は、ワークツリーの先頭を push するか。手元の origin/<ブランチ> が同じ先頭なら送らない（打ち直しで飛ばす）。
func (p *preparer) pushPlan() string {
	head := git.Head(p.wt, git.Options{})
	if head != "" && head == git.Commit(p.wt, "refs/remotes/origin/"+p.a.Branch, git.Options{}) {
		return "skip"
	}
	return "needed"
}

// Fetched は、続けるブランチを origin から取った結果を読む（sh が ccnavi-git.sh fetch を LC_ALL=C で打ち、終了コードと
// 出力を渡す）。答えは `remote checked|gone|unchecked`。gone は origin にそのブランチが無い。取れず、origin に無いとも
// 言えず、手元にも無ければ止める（fetch-failed-branch）。
func Fetched(root, cwd, branch string, code int, out string) (string, int) {
	var o outcome
	switch {
	case code == 0:
		o.Val("remote", "checked")
	case refusal.NoRemoteRef(out):
		o.Val("remote", "gone")
	case (repo{cwd}).refExists("refs/heads/" + branch):
		o.Val("remote", "unchecked")
	default:
		why := ""
		if lines := ospath.Splitlines(out); len(lines) > 0 {
			why = lines[0]
		}
		stop, _ := Stop(root, "fetch-failed-branch", map[string]string{"fb_branch": branch, "why": why})
		return stop, 0
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
		p.o.Err(strings.TrimRight(done.Stderr+done.Out, "\n"))
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
		p.o.Fail("worktree-busy", exitUnmet, "ワークツリーの作成先 "+dest+" が既にあり、ブランチ "+p.a.Branch+" のものではありません（現在のブランチ："+cur+"）。中を確かめ、不要なら片付けてから再実行してください。")
		return false
	}
	if p.a.From == "" && r.branchRef(p.a.Branch) == "" {
		p.o.Fail("branch-missing", exitUnmet, "ブランチ "+p.a.Branch+" が手元にも origin にもありません。ホストにだけある MR の元ブランチ（消された後など）かもしれません。")
		return false
	}
	if said, ok := treeadd.Add(p.root, p.cwd, dest, p.a.Branch, p.a.From); !ok {
		p.o.Err(said)
		p.o.Fail("worktree-failed", exitUnmet, "ワークツリーを作成できませんでした（上の出力を確認してください）。")
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
		p.o.Out(texts.Say(p.root, "NEXT_START_IDENT", texts.Values{"real_args_q": p.a.Real}, nil))
		p.o.Fail("ident-missing", exitUnmet, "git の作成者情報（user.name と user.email）が無いため、初期コミットを積めません。")
		return false
	}
	if !p.run(p.wt, "commit", "--allow-empty", "-m", InitSubject(p.a.Number)) {
		p.cleanup()
		p.o.Fail("commit-failed", exitUnmet, "初期コミットを積めなかった（上の出力を見てください）。")
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
		p.o.Out(texts.Say(p.root, "NOTE_START_WORKTREE_KEPT", texts.Values{"wt": p.wt, "branch": p.a.Branch}, nil))
		return
	}
	if !git.Run(p.cwd, []string{"branch", "-d", p.a.Branch}, git.Options{}).OK() {
		p.o.Out(texts.Say(p.root, "NOTE_START_BRANCH_KEPT", texts.Values{"branch": p.a.Branch}, nil))
	}
}
