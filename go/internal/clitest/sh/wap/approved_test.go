package wap

import (
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/shkit"
	"ccnavi/internal/infra/env"
)

// ccnavi-user-push-approved.sh と ccnavi-user-agree.sh の受入テスト。使い捨てのワークスペースを組み立てて sh を外から呼ぶ。
//
//  1. 置き場（.ccnavi/approved）の変更だけをコミットし、同じツリーの他の未コミットはコミットしない
//  2. コミットするものが無ければ 0 で「コミットして push する承認済みチケットはありません。」
//  3. main の上のツリーはコミットして push しない（0、標準エラーにブランチ名）
//  4. push が失敗すると 1、コミットは残る
//  5. detached のツリーは飛ばす
//  6. ccnavi-user-agree.sh が承認のあとコミットして push する。並べた識別子を user agree の後ろに渡し、- で始まる語と空の語は断る
//
// 承認そのものは実行ファイルの代わりに、承認済みチケットを 1 枚置くだけの sh（approveStub）で済ませる。承認の中身は他のテストが見ていて、
// ここで見るのは sh のつなぎ方だけ。読み返すのは終了コード・出力と、git に残ったものだけ。

const (
	approvedDoing  = ".ccnavi/approved/doing"
	carryMessage   = "ccnavi: 承認済みチケットを更新"
	nothingToCarry = "コミットして push する承認済みチケットはありません。"
)

// notWriteAndPush は write-and-push の対象かを答えるだけの実行ファイルの代わり（いつも対象外。ここで見るのは対象外の道）。
// push のゲート・統合先・git が断った理由は本物の実行ファイルが答える。
const notWriteAndPush = `#!/bin/sh
case " $* " in
*" --version "*) printf 'compat: @COMPAT@\n'; exit 0 ;;
*" write-and-push family "*) printf 'write-and-push 1\nfamily i0001\ntarget no\n'; exit 0 ;;
*" tree gate "* | *" git integration "* | *" git refusal "*) exec '@EXE@' "$@" ;;
*" write-and-push unsent "*) exec '@EXE@' "$@" ;;
esac
exit 0
`

// approveStub は承認の代わり。STUB_ARGS があれば受けた引数を 1 行ずつ書き、STUB_EXIT が 0 でなければ失敗し、STUB_TREE があればそこに承認済みチケットを置く。
const approveStub = `#!/bin/sh
case " $* " in
*" --version "*) printf 'compat: @COMPAT@\n'; exit 0 ;;
*" write-and-push family "*) printf 'write-and-push 1\nfamily i0001\ntarget no\n'; exit 0 ;;
# 未送信のパスは本物の実行ファイルが答える。
*" write-and-push unsent "*) exec '@EXE@' "$@" ;;
esac
[ -z "${STUB_ARGS:-}" ] || printf '%s\n' "$@" > "$STUB_ARGS"
# 実行ファイルは書き込みに入ると、書けたパスの一覧を --placed-out のファイルへ書く。
# --record-writes のファイルには、書いたパス（ルートからの相対）を書く（書かなければ空）。
record=""
while [ $# -gt 0 ]; do
	if [ "$1" = --placed-out ] && [ -n "${STUB_REPORT:-}" ]; then
		printf '%s\n' "$STUB_REPORT" > "$2"
	fi
	if [ "$1" = --record-writes ]; then
		record="$2"
		: > "$record"
	fi
	shift
done
[ "${STUB_EXIT:-0}" = 0 ] || exit "$STUB_EXIT"
[ -n "${STUB_TREE:-}" ] || exit 0
mkdir -p "$STUB_TREE/.ccnavi/approved/doing"
printf 'approved\n' > "$STUB_TREE/.ccnavi/approved/doing/i0001.md"
[ -z "$record" ] || printf '%s/.ccnavi/approved/doing/i0001.md\n' \
	"${STUB_REL:-.claude/worktrees/i0001}" > "$record"
exit "${STUB_AFTER_EXIT:-0}"
`

// pushKit は使い捨てのワークスペース（main）と bare のリモート。
type pushKit struct {
	t      *testing.T
	tmp    string
	ws     string
	remote string
	stub   string // write-and-push の対象外を答える実行ファイルの代わり
	exe    string
}

// newPushKit はワークスペースを組む。withAgree なら ccnavi-user-agree.sh も写す。
func newPushKit(t *testing.T, withAgree bool) *pushKit {
	t.Helper()
	shkit.Need(t, "sh", "git")
	t.Parallel()
	p := &pushKit{t: t, tmp: base.TempDir(t), exe: exe.ShPath(t)}
	p.ws = filepath.Join(p.tmp, "ws")
	names := []string{pushApproved}
	if withAgree {
		names = append(names, "ccnavi-user-agree.sh")
	}
	shkit.CopyScripts(t, p.ws, names...)
	run.Git(t, p.tmp, "init", "-q", "-b", "main", p.ws)
	shkit.Identity(p.t, p.ws)
	shkit.Write(t, filepath.Join(p.ws, ".gitignore"), ".claude/worktrees/\n")
	shkit.Write(t, filepath.Join(p.ws, "README.md"), "seed\n")
	run.Git(t, p.ws, "add", "--", ".gitignore", "README.md", ".ccnavi/scripts")
	run.Git(t, p.ws, "commit", "-q", "-m", "seed")
	p.remote = filepath.Join(p.tmp, "origin.git")
	run.Git(t, p.tmp, "init", "-q", "--bare", p.remote)
	run.Git(t, p.ws, "remote", "add", "origin", p.remote)
	p.stub = shkit.WriteExec(t, filepath.Join(p.tmp, "write-and-push-bin", "ccnavi"),
		fill(notWriteAndPush, "COMPAT", exe.Compat(t), "EXE", p.exe))
	return p
}

func (p *pushKit) git(dir string, args ...string) string {
	p.t.Helper()
	return run.Git(p.t, dir, args...)
}

func (p *pushKit) worktree(name string, detach bool) string {
	path := filepath.Join(p.ws, ".claude", "worktrees", name)
	if detach {
		p.git(p.ws, "worktree", "add", "-q", "--detach", path, "main")
	} else {
		p.git(p.ws, "worktree", "add", "-q", path, "-b", name, "main")
	}
	return path
}

// place は承認済みの親チケット（着手前。push のゲートは最初の push として通す）を置く。
func (p *pushKit) place(tree, ticket string) string {
	return shkit.Write(p.t, filepath.Join(tree, parts(approvedDoing), ticket+".md"), "---\nversion: 1\nticket: "+ticket+"\ntitle: t\n---\n")
}

func (p *pushKit) env(extra map[string]string) map[string]string {
	out := map[string]string{env.EntryPath: shkit.Slash(p.stub)}
	for name, value := range extra {
		out[name] = value
	}
	return out
}

func (p *pushKit) runSh(name string, o opts, args ...string) run.Result {
	p.t.Helper()
	e := o.env
	if e == nil {
		e = p.env(nil)
	}
	return run.Sh(p.t, filepath.Join(p.ws, ".ccnavi", "scripts", name), args, run.Opts{Dir: orDir(o.dir, p.ws), Env: e})
}

func (p *pushKit) push(args ...string) run.Result {
	p.t.Helper()
	return p.runSh(pushApproved, opts{}, args...)
}

// repository はワークスペースの下の別のリポジトリに bare のリモートを付けて、リモートのパスを返す。
func (p *pushKit) repository(path, branch string) string {
	p.git(p.tmp, "init", "-q", "-b", "main", path)
	shkit.Identity(p.t, path)
	shkit.Write(p.t, filepath.Join(path, "README.md"), "project\n")
	p.git(path, "add", "--", "README.md")
	p.git(path, "commit", "-q", "-m", "seed")
	p.git(path, "checkout", "-q", "-b", branch)
	remote := filepath.Join(p.tmp, filepath.Base(path)+".git")
	p.git(p.tmp, "init", "-q", "--bare", remote)
	p.git(path, "remote", "add", "origin", remote)
	return remote
}

func (p *pushKit) headOf(remote, branch string) string {
	r := run.Command(p.t, "git", []string{"--git-dir", remote, "rev-parse", "--verify", "-q", "refs/heads/" + branch}, run.Opts{Dir: p.tmp})
	if r.Code != 0 {
		return ""
	}
	return strings.TrimSpace(r.Stdout)
}

func (p *pushKit) remoteHead(branch string) string { return p.headOf(p.remote, branch) }
func (p *pushKit) staged(tree string) string       { return p.git(tree, "diff", "--cached", "--name-only") }
func (p *pushKit) head(tree string) string         { return p.git(tree, "rev-parse", "HEAD") }
func (p *pushKit) subject(tree string) string      { return p.git(tree, "log", "-1", "--format=%s") }

func (p *pushKit) committed(tree string) []string {
	var out []string
	for _, line := range strings.Split(p.git(tree, "show", "--name-only", "--format=", "HEAD"), "\n") {
		if line != "" {
			out = append(out, line)
		}
	}
	return out
}

func (p *pushKit) dirty(tree string, paths ...string) string {
	return p.git(tree, append([]string{"status", "--porcelain", "--"}, paths...)...)
}

// said は sh 自身が標準エラーに name を名指ししたか。git のエラー文に紛れた名前は数えない。
func said(r run.Result, name string) bool {
	for _, line := range strings.Split(r.Stderr, "\n") {
		if strings.HasPrefix(line, "ccnavi-user-push-approved:") && strings.Contains(line, name) {
			return true
		}
	}
	return false
}

func sorted(s ...string) []string {
	out := append([]string{}, s...)
	sort.Strings(out)
	return out
}

// ---- 置き場だけをコミットする

// 置き場の変更だけをコミットして push する。同じツリーの書きかけ（追跡しているファイルの変更・未追跡のファイル）はコミットせず、
// 実行後も書きかけのまま（ステージもしない）残す。
func TestCommitsOnlyTheApprovedPlaceAndPushes(t *testing.T) {
	p := newPushKit(t, false)
	tree := p.worktree(parentID, false)
	p.place(tree, parentID)
	shkit.Write(t, filepath.Join(tree, "README.md"), "書きかけ\n")
	shkit.Write(t, filepath.Join(tree, "src", "draft.py"), "x\n")
	r := p.push()
	code(t, r, 0)
	eq(t, "件名", p.subject(tree), carryMessage)
	eqList(t, "コミット", p.committed(tree), []string{approvedDoing + "/i0001.md"})
	eq(t, "README の状態", p.dirty(tree, "README.md"), "M README.md")
	has(t, p.dirty(tree, "src"), "??")
	eq(t, "索引", p.staged(tree), "")
	eq(t, "リモート", p.remoteHead(parentID), p.head(tree))
	has(t, r.Stdout, parentID)
	lacks(t, r.Stdout, nothingToCarry)
}

// 先にステージされていた他人の変更もコミットしない。パスを限るのはコミットまで。
func TestDoesNotCarryChangesSomeoneElseStaged(t *testing.T) {
	p := newPushKit(t, false)
	tree := p.worktree(parentID, false)
	p.place(tree, parentID)
	shkit.Write(t, filepath.Join(tree, "README.md"), "ステージ済みの書きかけ\n")
	p.git(tree, "add", "--", "README.md")
	code(t, p.push(), 0)
	eq(t, "件名", p.subject(tree), carryMessage)
	eqList(t, "コミット", p.committed(tree), []string{approvedDoing + "/i0001.md"})
	if p.dirty(tree, "README.md") == "" {
		t.Error("他人の変更までコミットした")
	}
}

// ボードの保存が残した flows/.<名前>.<番号>.tmp はコミットせず、フローはコミットする。
func TestLeftoverFlowTempFilesAreNotCarried(t *testing.T) {
	p := newPushKit(t, false)
	tree := p.worktree(parentID, false)
	flowsDir := filepath.Join(tree, ".ccnavi", "approved", "flows")
	shkit.Write(t, filepath.Join(flowsDir, "i0001-01-01.yml"), "nodes: []\n")
	temp := shkit.Write(t, filepath.Join(flowsDir, ".i0001-01-01.yml.123.abcdef.tmp"), "half")
	code(t, p.push(), 0)
	eqList(t, "コミット", p.committed(tree), []string{".ccnavi/approved/flows/i0001-01-01.yml"})
	if !shkit.Exists(temp) {
		t.Error("一時ファイルを消した")
	}
	eq(t, "索引", p.staged(tree), "")
	// 一時ファイルだけが残っていても、コミットするものは無い。
	again := p.push()
	code(t, again, 0)
	has(t, again.Stdout, nothingToCarry)
}

// 取り込んで消えた下書き（wip/proposals/flows/）は、フローと一緒にコミットして push する。書き直された下書き・追跡していない下書きには触れない。
func TestARemovedDraftIsCarriedWithTheFlow(t *testing.T) {
	p := newPushKit(t, false)
	tree := p.worktree(parentID, false)
	drafts := "wip/proposals/flows"
	taken := drafts + "/i0001-01-01.yml"
	rewritten := drafts + "/i0001-01-02.yml"
	untracked := drafts + "/i0001-01-03.yml"
	for _, rel := range []string{taken, rewritten} {
		shkit.Write(t, filepath.Join(tree, parts(rel)), "nodes: []\n")
	}
	p.git(tree, "add", "--", taken, rewritten)
	p.git(tree, "commit", "-q", "-m", "drafts")
	flow := ".ccnavi/approved/flows/i0001-01-01.yml"
	shkit.Write(t, filepath.Join(tree, parts(flow)), "nodes: []\n")
	shkit.Remove(t, filepath.Join(tree, parts(taken)))
	shkit.Write(t, filepath.Join(tree, parts(rewritten)), "nodes: [x]\n")
	shkit.Write(t, filepath.Join(tree, parts(untracked)), "nodes: []\n")
	r := p.push()
	code(t, r, 0)
	// 同じ中身なので、改名として読まずに 2 つのパスで見る。
	got := sorted(strings.Fields(p.git(tree, "show", "--name-only", "--no-renames", "--format=", "HEAD"))...)
	eqList(t, "コミット", got, sorted(flow, taken))
	if p.dirty(tree, rewritten) == "" || p.dirty(tree, untracked) == "" {
		t.Error("書き直された下書きか追跡していない下書きに触れた")
	}
	eq(t, "取り込んだ下書き", p.dirty(tree, taken), "")
	eq(t, "索引", p.staged(tree), "")
	eq(t, "リモート", p.remoteHead(parentID), p.head(tree))
}

// フローの保存が無ければコミットしない（置き場の変更が無いツリーには入らない）。
func TestDraftsAloneAreNotCarried(t *testing.T) {
	p := newPushKit(t, false)
	tree := p.worktree(parentID, false)
	taken := "wip/proposals/flows/i0001-01-01.yml"
	shkit.Write(t, filepath.Join(tree, parts(taken)), "nodes: []\n")
	p.git(tree, "add", "--", taken)
	p.git(tree, "commit", "-q", "-m", "draft")
	shkit.Remove(t, filepath.Join(tree, parts(taken)))
	before := p.head(tree)
	r := p.push()
	code(t, r, 0)
	has(t, r.Stdout, nothingToCarry)
	eq(t, "HEAD", p.head(tree), before)
}

// ---- コミットするものが無い

func TestNothingToCarrySaysSo(t *testing.T) {
	p := newPushKit(t, false)
	tree := p.worktree(parentID, false)
	// 置き場の外の書きかけはコミットするものに数えない。
	shkit.Write(t, filepath.Join(tree, "README.md"), "書きかけ\n")
	before := p.head(tree)
	r := p.push()
	code(t, r, 0)
	has(t, r.Stdout, nothingToCarry)
	eq(t, "HEAD", p.head(tree), before)
	eq(t, "リモート", p.remoteHead(parentID), "")
}

// ---- 保護されたブランチ（統合先）

func TestMainIsCommittedButNotPushed(t *testing.T) {
	p := newPushKit(t, false)
	p.place(p.ws, parentID)
	r := p.push()
	code(t, r, 0)
	eq(t, "件名", p.subject(p.ws), carryMessage)
	eqList(t, "コミット", p.committed(p.ws), []string{approvedDoing + "/i0001.md"})
	eq(t, "リモート", p.remoteHead("main"), "")
	has(t, r.Stderr, "main")
}

// assertNotPushed は統合先のブランチはコミットだけして、push しないこと。
func (p *pushKit) assertNotPushed(r run.Result, tree, branch string) {
	p.t.Helper()
	code(p.t, r, 0)
	eq(p.t, "件名", p.subject(tree), carryMessage)
	eq(p.t, "リモート", p.remoteHead(branch), "")
	has(p.t, r.Stderr, "統合先 "+branch)
}

// 固定のリストに無い名前の統合先（origin/HEAD が指すブランチ）は、コミットして push しない。決め方は ccnavi_integration。
func TestTheBranchOriginHeadPointsToIsCommittedButNotPushed(t *testing.T) {
	p := newPushKit(t, false)
	tree := p.worktree("develop-v1.0.0", false)
	ref := "refs/remotes/origin/develop-v1.0.0"
	p.git(p.ws, "update-ref", ref, "HEAD")
	p.git(p.ws, "symbolic-ref", "refs/remotes/origin/HEAD", ref)
	p.place(tree, parentID)
	p.assertNotPushed(p.push(), tree, "develop-v1.0.0")
}

// 統合先が決まらない（origin/HEAD も origin/main・master も無い）なら今までどおり送る。
func TestAnUndeterminedIntegrationKeepsTheFixedListOnly(t *testing.T) {
	p := newPushKit(t, false)
	tree := p.worktree("develop-v1.0.0", false)
	p.place(tree, parentID)
	code(t, p.push(), 0)
	eq(t, "リモート", p.remoteHead("develop-v1.0.0"), p.head(tree))
}

// projects/<名前>/ のツリーは、そのプロジェクトの origin/HEAD で統合先を決める。ワークスペースの origin/HEAD はプロジェクトの
// 統合先を変えない（固定のリストに無い名前 develop-v1.0.0 で止まる）。
func TestAProjectIsJudgedByItsOwnIntegration(t *testing.T) {
	p := newPushKit(t, false)
	project := filepath.Join(p.ws, "projects", "app")
	remote := p.repository(project, "develop-v1.0.0")
	p.place(project, parentID)
	// ワークスペースの origin/HEAD が同じ名前でも、プロジェクトの統合先は別（origin も空）。
	ref := "refs/remotes/origin/develop-v1.0.0"
	p.git(p.ws, "update-ref", ref, "HEAD")
	p.git(p.ws, "symbolic-ref", "refs/remotes/origin/HEAD", ref)
	code(t, p.push(), 0)
	eq(t, "プロジェクトのリモート", p.headOf(remote, "develop-v1.0.0"), p.head(project))

	// プロジェクト側の origin/HEAD が develop-v1.0.0 なら、そのプロジェクトでは止まる。
	shkit.Write(t, filepath.Join(project, parts(approvedDoing), "i0002.md"), "approved\n")
	before := p.headOf(remote, "develop-v1.0.0")
	p.git(project, "update-ref", ref, "HEAD")
	p.git(project, "symbolic-ref", "refs/remotes/origin/HEAD", ref)
	r := p.push()
	code(t, r, 0)
	has(t, r.Stderr, "統合先 develop-v1.0.0")
	eq(t, "プロジェクトのリモート", p.headOf(remote, "develop-v1.0.0"), before)
	if p.head(project) == before {
		t.Error("コミットしていない")
	}
}

// ---- push が失敗する

func TestFailedPushExits1AndKeepsTheCommit(t *testing.T) {
	p := newPushKit(t, false)
	p.git(p.ws, "remote", "set-url", "origin", filepath.Join(p.tmp, "missing.git"))
	tree := p.worktree(parentID, false)
	p.place(tree, parentID)
	r := p.push()
	code(t, r, 1)
	eq(t, "件名", p.subject(tree), carryMessage)
	eq(t, "置き場の未コミット", p.dirty(tree, approvedDoing), "")
	has(t, r.Stderr, parentID)
	// 失敗の理由（git の標準エラーの 1 行目）を添え、手で送る道も言う。
	has(t, r.Stderr, "missing.git")
	has(t, r.Stderr, "git push -u origin "+parentID)
}

// 送る側が失敗の終了コードを返しても、リモートに先頭が届いていれば成功として扱う（ls-remote で確かめる）。
func TestAPushThatFailedAfterArrivingCountsAsSent(t *testing.T) {
	p := newPushKit(t, false)
	tree := p.worktree(parentID, false)
	p.place(tree, parentID)
	realGit, err := exec.LookPath("git")
	if err != nil {
		t.Skip("git が見つからない")
	}
	bin := filepath.Join(p.tmp, "gitwrap")
	shkit.WriteExec(t, filepath.Join(bin, "git"),
		"#!/bin/sh\n'"+shkit.Slash(realGit)+"' \"$@\"\nrc=$?\ncase \" $* \" in *\" push \"*) exit 1 ;; esac\nexit $rc\n")
	e := p.env(map[string]string{"PATH": shkit.Slash(bin) + string(os.PathListSeparator) + os.Getenv("PATH")})
	r := p.runSh(pushApproved, opts{env: e})
	code(t, r, 0)
	eq(t, "リモート", p.remoteHead(parentID), p.head(tree))
}

// ---- 親のブランチの push のゲート（ccnavi_push_gate）
//
// 掛けるか・形跡があるかの判定は実行ファイル（tree gate）の go test が見る。ここでは通しで、ゲートが ls-remote を頼んだときに
// sh が打って止めること、通すときに送ることを確かめる。

// sentParent は送ったことのある親のワークツリー。ホストがブランチを消した（参照だけが残る）形にする。
func (p *pushKit) sentParent() string {
	tree := p.worktree(parentID, false)
	p.git(tree, "push", "-q", "-u", "origin", parentID)
	p.git(p.tmp, "--git-dir", p.remote, "update-ref", "-d", "refs/heads/"+parentID)
	// 着手済みの承認済み親チケット（送った形跡になる）
	shkit.Write(p.t, filepath.Join(tree, parts(approvedDoing), parentID+".md"),
		"---\nversion: 1\nticket: "+parentID+"\nstarted_at: 2026-01-01T00:00:00Z\ntitle: 親\n---\n")
	return tree
}

func TestAParentGoneFromTheRemoteIsCommittedButNotPushed(t *testing.T) {
	p := newPushKit(t, false)
	tree := p.sentParent()
	r := p.push()
	code(t, r, 1)
	eq(t, "件名", p.subject(tree), carryMessage)
	eq(t, "リモート", p.remoteHead(parentID), "")
	has(t, r.Stderr, "親のブランチ i0001 がリモートに無い")
	has(t, r.Stderr, "ccnavi-sync.sh i0001")
	has(t, r.Stderr, "コミットはローカルに残っている")
	// ユーザがターミナルで元の名前に戻せば、打ち直して送れる。
	p.git(tree, "push", "-q", "origin", parentID)
	shkit.Write(t, filepath.Join(tree, parts(approvedDoing), "i0002.md"), "approved\n")
	code(t, p.push(), 0)
	eq(t, "リモート", p.remoteHead(parentID), p.head(tree))
}

func TestARemoteThatCannotBeAskedIsRefused(t *testing.T) {
	p := newPushKit(t, false)
	tree := p.sentParent()
	p.git(p.ws, "remote", "set-url", "origin", filepath.Join(p.tmp, "missing.git"))
	r := p.push()
	code(t, r, 1)
	has(t, r.Stderr, "リモートにあるかを確かめられない")
	eq(t, "件名", p.subject(tree), carryMessage)
}

// 実行ファイルが答えないときも送る（実行ファイルを起こさない作り）。手元に origin の参照があるブランチだけは ls-remote で確かめ、
// リモートから消えていれば送らない。
func TestWithoutTheExecutableOnlyATrackedBranchIsChecked(t *testing.T) {
	p := newPushKit(t, false)
	project := filepath.Join(p.ws, "projects", "app")
	remote := p.repository(project, "work")
	p.git(project, "push", "-q", "-u", "origin", "work")
	p.git(p.tmp, "--git-dir", remote, "update-ref", "-d", "refs/heads/work")
	p.place(project, parentID)
	gone := p.env(map[string]string{env.EntryPath: shkit.Slash(filepath.Join(p.tmp, "no-such-bin"))})
	r := p.runSh(pushApproved, opts{env: gone})
	code(t, r, 1)
	has(t, r.Stderr, "実行ファイルが答えないため")
	eq(t, "プロジェクトのリモート", p.headOf(remote, "work"), "")
	p.git(project, "update-ref", "-d", "refs/remotes/origin/work")
	p.place(project, "i0002")
	code(t, p.runSh(pushApproved, opts{env: gone}), 0)
	eq(t, "プロジェクトのリモート", p.headOf(remote, "work"), p.head(project))
}

// ---- detached・projects・リンク

// detached のツリーは飛ばし、ほかのツリーはコミットして push する。
func TestDetachedTreeIsSkippedAndOthersAreCarried(t *testing.T) {
	p := newPushKit(t, false)
	loose := p.worktree("loose", true)
	p.place(loose, parentID)
	before := p.head(loose)
	tree := p.worktree("i0002", false)
	p.place(tree, "i0002")
	r := p.push()
	// 飛ばしたツリーがあるときの終了コードは設計で決まっていないので見ない。
	has(t, r.Stderr, "loose")
	eq(t, "detached の HEAD", p.head(loose), before)
	if p.dirty(loose, approvedDoing) == "" {
		t.Error("detached のツリーに触れた")
	}
	eq(t, "件名", p.subject(tree), carryMessage)
	eq(t, "リモート", p.remoteHead("i0002"), p.head(tree))
}

// detached のツリーにしか変更が無いときは 0 で終わる。「コミットして push する承認済みチケットは無い。」とは言わず、飛ばしたことを言う。
func TestOnlyADetachedTreeIsSkippedWithoutSayingNothing(t *testing.T) {
	p := newPushKit(t, false)
	loose := p.worktree("loose", true)
	p.place(loose, parentID)
	before := p.head(loose)
	r := p.push()
	code(t, r, 0)
	lacks(t, r.Stdout, nothingToCarry)
	lacks(t, r.Stderr, nothingToCarry)
	has(t, r.Stderr, "loose")
	eq(t, "HEAD", p.head(loose), before)
	if p.dirty(loose, approvedDoing) == "" {
		t.Error("detached のツリーに触れた")
	}
}

// projects/<名前>/ の下のツリーの置き場も、コミットして push する。
func TestCarriesATreeUnderProjects(t *testing.T) {
	p := newPushKit(t, false)
	project := filepath.Join(p.ws, "projects", "app")
	remote := p.repository(project, "work")
	p.place(project, parentID)
	r := p.push()
	code(t, r, 0)
	eq(t, "件名", p.subject(project), carryMessage)
	eqList(t, "コミット", p.committed(project), []string{approvedDoing + "/i0001.md"})
	eq(t, "プロジェクトのリモート", p.headOf(remote, "work"), p.head(project))
	lacks(t, r.Stdout, nothingToCarry)
}

// 1 本のツリーで git add が失敗しても、もう 1 本はコミットして push する。終了コードは 1 で、失敗したツリーを標準エラーで名指しする。
func TestAFailedAddInOneTreeDoesNotStopTheOthers(t *testing.T) {
	p := newPushKit(t, false)
	locked := p.worktree("locked", false)
	p.place(locked, parentID)
	before := p.head(locked)
	// そのツリーのインデックスを他のプロセスが握っている形。git add が失敗する。
	shkit.Write(t, filepath.Join(p.git(locked, "rev-parse", "--absolute-git-dir"), "index.lock"), "")
	tree := p.worktree("i0002", false)
	p.place(tree, "i0002")
	r := p.push()
	code(t, r, 1)
	if !said(r, "locked") {
		t.Errorf("失敗したツリーを名指ししていない:\n%s", r.Stderr)
	}
	eq(t, "失敗したツリーの HEAD", p.head(locked), before)
	eq(t, "失敗したツリーのリモート", p.remoteHead("locked"), "")
	// 失敗したツリーのあとでも、他のツリーはコミットして push する。
	eq(t, "件名", p.subject(tree), carryMessage)
	eqList(t, "コミット", p.committed(tree), []string{approvedDoing + "/i0002.md"})
	eq(t, "リモート", p.remoteHead("i0002"), p.head(tree))
}

func (p *pushKit) symlinkOrSkip(target, link string) {
	p.t.Helper()
	if !base.CanSymlink() {
		p.t.Skip("シンボリックリンクが作れない")
	}
	if err := os.MkdirAll(filepath.Dir(link), 0o755); err != nil {
		p.t.Fatal(err)
	}
	if err := os.Symlink(target, link); err != nil {
		p.t.Skipf("シンボリックリンクが作れない: %v", err)
	}
}

// outsideRepository はワークスペースの外の holder/app にリポジトリを置き、承認済みチケットを 1 枚置く。
func (p *pushKit) outsideRepository(holder string) (app, remote string) {
	app = filepath.Join(p.tmp, holder, "app")
	remote = p.repository(app, "work")
	p.place(app, parentID)
	return app, remote
}

func (p *pushKit) assertNotCarried(app, remote, before string) {
	p.t.Helper()
	eq(p.t, "外のリポジトリの HEAD", p.head(app), before)
	if p.dirty(app, approvedDoing) == "" {
		p.t.Error("外のリポジトリにコミットした")
	}
	eq(p.t, "外のリポジトリのリモート", p.headOf(remote, "work"), "")
}

// .claude/worktrees/ の下のシンボリックリンクは辿らない。標準エラーに言う。リンク先はワークスペースの外のリポジトリ。
// 実際のワークツリーはコミットして push する。
func TestASymlinkUnderWorktreesIsNotFollowed(t *testing.T) {
	p := newPushKit(t, false)
	app, remote := p.outsideRepository("outside")
	before := p.head(app)
	tree := p.worktree("i0002", false)
	p.place(tree, "i0002")
	p.symlinkOrSkip(app, filepath.Join(p.ws, ".claude", "worktrees", "linked"))
	r := p.push()
	// 飛ばしたリンクがあるときの終了コードは決まっていないので見ない。
	if !said(r, "linked") {
		t.Errorf("リンクを名指ししていない:\n%s", r.Stderr)
	}
	p.assertNotCarried(app, remote, before)
	eq(t, "件名", p.subject(tree), carryMessage)
	eq(t, "リモート", p.remoteHead("i0002"), p.head(tree))
}

// projects/ そのものがシンボリックリンクなら、その下のリポジトリにコミットしない。リンク先の中の 1 件ずつはリンクではないので、
// 置き場の段階で確かめないと辿ってしまう。飛ばしたことは標準エラーに言う。実際のワークツリーはコミットして push する。
func TestProjectsItselfAsASymlinkIsNotFollowed(t *testing.T) {
	p := newPushKit(t, false)
	app, remote := p.outsideRepository("elsewhere-projects")
	before := p.head(app)
	tree := p.worktree("i0002", false)
	p.place(tree, "i0002")
	p.symlinkOrSkip(filepath.Dir(app), filepath.Join(p.ws, "projects"))
	r := p.push()
	if !said(r, "projects") {
		t.Errorf("projects を名指ししていない:\n%s", r.Stderr)
	}
	p.assertNotCarried(app, remote, before)
	eq(t, "件名", p.subject(tree), carryMessage)
	eq(t, "リモート", p.remoteHead("i0002"), p.head(tree))
}

// .claude/worktrees/ そのものがシンボリックリンクなら、その下にコミットしない。
func TestWorktreesItselfAsASymlinkIsNotFollowed(t *testing.T) {
	p := newPushKit(t, false)
	app, remote := p.outsideRepository("elsewhere-worktrees")
	before := p.head(app)
	p.symlinkOrSkip(filepath.Dir(app), filepath.Join(p.ws, ".claude", "worktrees"))
	r := p.push()
	if !said(r, "worktrees") {
		t.Errorf("worktrees を名指ししていない:\n%s", r.Stderr)
	}
	p.assertNotCarried(app, remote, before)
}

// ---- 引数と置き場

// --path を並べると、置き場の変更のうちそのパスだけをコミットして push する。
func TestPathCarriesOnlyTheNamedPaths(t *testing.T) {
	p := newPushKit(t, false)
	tree := p.worktree(parentID, false)
	p.place(tree, parentID)
	p.place(tree, "i0009")
	gone := shkit.Write(t, filepath.Join(tree, "wip", "proposals", "todo", "i0001.md"), "proposal\n")
	p.git(tree, "add", "--", gone)
	p.git(tree, "commit", "-q", "-m", "approval-request")
	shkit.Remove(t, gone)
	review := shkit.Write(t, filepath.Join(tree, "wip", "proposals", "review", "i0008.md"), "review\n")
	other := p.worktree("i0002", false)
	p.place(other, "i0002")
	rel := ".claude/worktrees/i0001"
	r := p.push("--path", rel+"/"+approvedDoing+"/i0001.md", "--path", rel+"/wip/proposals/todo/i0001.md")
	code(t, r, 0)
	got := p.committed(tree)
	eqList(t, "コミット", sorted(got...), sorted(approvedDoing+"/i0001.md", "wip/proposals/todo/i0001.md"))
	eq(t, "リモート", p.remoteHead(parentID), p.head(tree))
	if p.dirty(tree, approvedDoing+"/i0009.md") == "" || p.dirty(tree, review) == "" {
		t.Error("名指ししていない変更までコミットした")
	}
	eq(t, "別のツリーのリモート", p.remoteHead("i0002"), "")
	if p.dirty(other, approvedDoing+"/i0002.md") == "" {
		t.Error("別のツリーにコミットした")
	}
}

func TestPathRefusesAbsolutePathsAndParentIdsTogether(t *testing.T) {
	p := newPushKit(t, false)
	p.worktree(parentID, false)
	for _, args := range [][]string{{"--path"}, {"--path", "/abs"}, {"--path", "../x"}, {"--path", "a", "i0001"}} {
		r := p.push(args...)
		if r.Code != 2 {
			t.Errorf("%q: 終了コード %d（2 のはず）\n%s%s", args, r.Code, r.Stdout, r.Stderr)
		}
	}
}

func TestTakesOnlyParentIdsAndNeedsTheWorkspace(t *testing.T) {
	p := newPushKit(t, false)
	code(t, p.push("--help"), 0)
	for _, word := range []string{"-x", "../i0001", "a/b", ""} {
		wrong := p.push(word)
		if wrong.Code != 2 {
			t.Errorf("%q: 終了コード %d（2 のはず）\n%s%s", word, wrong.Code, wrong.Stdout, wrong.Stderr)
		}
	}
	// 取り込み済みでない親子のチケットの名指しはコミットしない（今のまま、ユーザがコミットする）。
	tree := p.worktree(parentID, false)
	p.place(tree, parentID)
	named := p.push(parentID)
	code(t, named, 0)
	has(t, named.Stdout, "取り込み済みの親子のチケットでない")
	if p.dirty(tree, approvedDoing) == "" {
		t.Error("名指しでコミットした")
	}
	eq(t, "リモート", p.remoteHead(parentID), "")
	outside := filepath.Join(p.tmp, "elsewhere")
	if err := os.MkdirAll(outside, 0o755); err != nil {
		t.Fatal(err)
	}
	// cwd がワークスペースの外でも、sh 自身の置き場から根を見つける（消えたワークツリーに残った端末から打つ形）
	code(t, p.runSh(pushApproved, opts{dir: outside}), 0)
}

// ---- ccnavi-user-agree.sh

type approveKit struct{ *pushKit }

func newApproveKit(t *testing.T) *approveKit {
	p := newPushKit(t, true)
	p.stub = shkit.WriteExec(t, filepath.Join(p.tmp, "bin", "ccnavi"), fill(approveStub, "COMPAT", exe.Compat(t), "EXE", p.exe))
	return &approveKit{p}
}

func (a *approveKit) approve(extra map[string]string, ids ...string) run.Result {
	a.t.Helper()
	return a.runSh("ccnavi-user-agree.sh", opts{env: a.env(extra)}, ids...)
}

func (a *approveKit) received(path string) []string {
	return strings.Split(strings.TrimRight(shkit.Read(a.t, path), "\n"), "\n")
}

// 並べた識別子だけを承認の対象にする（同じ親の承認待ちのうち 1 本だけを先に承認する形）。識別子の無い承認は全部の承認待ちを対象にする。
// 識別子と並んだ help は識別子の 1 つで、何も言わずに破棄しない。
func TestApprovePassesTheIdsAfterUserAgree(t *testing.T) {
	cases := []struct {
		name string
		ids  []string
	}{
		{"識別子を並べる", []string{"i0002-01-03", "i0002-01-04"}},
		{"識別子なし", nil},
		{"help は識別子と並べば識別子", []string{"help", "i0002-01-01"}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			a := newApproveKit(t)
			a.worktree(parentID, false)
			args := filepath.Join(a.tmp, "args")
			okRun(t, a.approve(map[string]string{"STUB_ARGS": shkit.Slash(args)}, c.ids...))
			got := a.received(args)
			eqList(t, "先頭", got[:1], []string{"--root"})
			// 実行ファイルには、書いたパスの一覧（--record-writes）と、書けたパスの報告の書き出し先（--placed-out）も渡す
			eq(t, "書いたパスの一覧", got[2], "--record-writes")
			// 承認の入口（user agree）を、入口の印（--entry sh）を付けて呼ぶ
			eqList(t, "入口の印", got[4:6], []string{"--entry", "sh"})
			eqList(t, "入口", got[6:9], []string{"user", "agree", "--placed-out"})
			if len(c.ids) == 0 {
				eq(t, "識別子の数", len(got[10:]), 0)
			} else {
				eqList(t, "識別子", got[10:], c.ids)
			}
		})
	}
}

// 識別子でない語は断り、実行ファイルを呼ばない。--yes や --root を混ぜると、ターミナルの y/N を経ない経路や別のワークスペースになってしまう。
func TestApproveRefusesWordsThatAreNotIds(t *testing.T) {
	a := newApproveKit(t)
	tree := a.worktree(parentID, false)
	args := filepath.Join(a.tmp, "args")
	for _, ids := range [][]string{{"--yes", "i0002"}, {"i0002", "--root", "/elsewhere"}, {"-x"}, {""}, {"--help", "i0002-01-01"}} {
		r := a.approve(map[string]string{"STUB_ARGS": shkit.Slash(args), "STUB_TREE": shkit.Slash(tree)}, ids...)
		code(t, r, 2)
		has(t, r.Stderr, "チケットID以外の引数")
		if shkit.Exists(args) {
			t.Errorf("%q: 実行ファイルを呼んだ", ids)
		}
		eq(t, "リモート", a.remoteHead(parentID), "")
	}
}

func TestApproveHelpStillShowsUsage(t *testing.T) {
	a := newApproveKit(t)
	for _, word := range []string{"-h", "--help", "help"} {
		r := a.approve(nil, word)
		code(t, r, 0)
		has(t, r.Stdout, "<識別子>")
	}
}

func TestApproveCarriesAfterApproval(t *testing.T) {
	a := newApproveKit(t)
	tree := a.worktree(parentID, false)
	code(t, a.approve(map[string]string{"STUB_TREE": shkit.Slash(tree)}), 0)
	eq(t, "件名", a.subject(tree), carryMessage)
	eqList(t, "コミット", a.committed(tree), []string{approvedDoing + "/i0001.md"})
	eq(t, "リモート", a.remoteHead(parentID), a.head(tree))
}

// コミットと push の段階は ccnavi-user-push-approved.sh に任せる。コミットするものが無ければその 1 行が出る。
func TestApproveSaysWhenThereIsNothingToCarry(t *testing.T) {
	a := newApproveKit(t)
	a.worktree(parentID, false)
	r := a.approve(nil)
	code(t, r, 0)
	has(t, r.Stdout, nothingToCarry)
}

// 何も置かれなかった承認の失敗では、無関係な未コミットの承認済みチケットも含めて送らない。
func TestAFailedApprovalCarriesNothing(t *testing.T) {
	a := newApproveKit(t)
	tree := a.worktree(parentID, false)
	a.place(tree, parentID)
	before := a.head(tree)
	code(t, a.approve(map[string]string{"STUB_EXIT": "1"}), 1)
	eq(t, "HEAD", a.head(tree), before)
	eq(t, "リモート", a.remoteHead(parentID), "")
}

// 一部だけ通って非 0 で終わっても、置かれた分はコミットして push する。終了コードは 1。
// 親は承認済みとして残り、子の書き込みだけ失敗した形。送らないと他のマシンに届かない。
func TestAPartialApprovalStillCarriesWhatWasPlaced(t *testing.T) {
	a := newApproveKit(t)
	tree := a.worktree(parentID, false)
	r := a.approve(map[string]string{"STUB_TREE": shkit.Slash(tree), "STUB_AFTER_EXIT": "4"})
	code(t, r, 1)
	eq(t, "件名", a.subject(tree), carryMessage)
	eqList(t, "コミット", a.committed(tree), []string{approvedDoing + "/i0001.md"})
	eq(t, "リモート", a.remoteHead(parentID), a.head(tree))
	has(t, r.Stderr, "承認の一部だけが通りました")
}

// 親は書けて子だけ書けなかった承認。親は push され、書けた分と子の失敗が報告される。
func TestAPartialApprovalReportsTheWrittenAndTheFailed(t *testing.T) {
	a := newApproveKit(t)
	tree := a.worktree(parentID, false)
	report := `{"version":1,"placed":["/w/.ccnavi/approved/doing/i0001.md"],` +
		`"failed":[{"ticket":"i0001-01-01","reason":"保存できませんでした"}],"unwritten":[],"rejected":[]}`
	r := a.approve(map[string]string{"STUB_TREE": shkit.Slash(tree), "STUB_AFTER_EXIT": "4", "STUB_REPORT": report})
	code(t, r, 1)
	eqList(t, "コミット", a.committed(tree), []string{approvedDoing + "/i0001.md"})
	eq(t, "リモート", a.remoteHead(parentID), a.head(tree))
	has(t, r.Stderr, "/w/.ccnavi/approved/doing/i0001.md")
	has(t, r.Stderr, "i0001-01-01")
	has(t, r.Stderr, "保存できませんでした")
	lacks(t, r.Stderr, "未送信")
}

// 承認の結果に、親のワークツリーに残る提案以外の変更が出る。承認は通る（終了コード 0）。
func TestApproveShowsAChangeLeftInTheParentWorktree(t *testing.T) {
	a := newApproveKit(t)
	tree := a.worktree(parentID, false)
	report := `{"placed":["` + shkit.Slash(tree) + `/` + approvedDoing + `/i0001.md"],"failed":[],"unwritten":[],` +
		`"rejected":[],"dirty_parent":[{"ticket":"i0001","paths":["index.html"]}]}`
	r := a.approve(map[string]string{"STUB_TREE": shkit.Slash(tree), "STUB_REPORT": report})
	code(t, r, 0)
	has(t, r.Stderr, "dirty_parent=")
	has(t, r.Stderr, "index.html")
	// ユーザが打つ sh には「次:」を足さない（案内は拡張の prompt と status が担う）。
	lacks(t, r.Stdout+r.Stderr, "次:")
}

// 一部だけ書けて、push も失敗したときは、未送信のパスを名指しする。
func TestAPartialApprovalWhosePushFailsNamesTheUnsentPaths(t *testing.T) {
	a := newApproveKit(t)
	tree := a.worktree(parentID, false)
	a.git(a.ws, "remote", "set-url", "origin", filepath.Join(a.tmp, "missing.git"))
	r := a.approve(map[string]string{"STUB_TREE": shkit.Slash(tree), "STUB_AFTER_EXIT": "4", "STUB_REPORT": `{"placed":[]}`})
	code(t, r, 1)
	eq(t, "件名", a.subject(tree), carryMessage)
	has(t, r.Stderr, "未送信")
	has(t, r.Stderr, approvedDoing+"/i0001.md")
}

// 同じ置き場にある承認と無関係な変更（別の承認済みチケット、review/、別のツリー）は送らない。
func TestApproveCarriesOnlyWhatTheApprovalWrote(t *testing.T) {
	a := newApproveKit(t)
	tree := a.worktree(parentID, false)
	a.place(tree, "i0009")
	review := shkit.Write(t, filepath.Join(tree, "wip", "proposals", "review", "i0008.md"), "review\n")
	other := a.worktree("i0002", false)
	a.place(other, "i0002")
	r := a.approve(map[string]string{"STUB_TREE": shkit.Slash(tree)})
	code(t, r, 0)
	eqList(t, "コミット", a.committed(tree), []string{approvedDoing + "/i0001.md"})
	eq(t, "リモート", a.remoteHead(parentID), a.head(tree))
	if a.dirty(tree, approvedDoing+"/i0009.md") == "" {
		t.Error("無関係な承認済みチケットを送った")
	}
	if a.dirty(tree, review) == "" {
		t.Error("review/ の変更を送った")
	}
	eq(t, "別のツリーのリモート", a.remoteHead("i0002"), "")
	if a.dirty(other, approvedDoing+"/i0002.md") == "" {
		t.Error("別のツリーにコミットした")
	}
	lacks(t, r.Stderr, "済んでいない")
}

// 何も置かずに失敗した（4 以外の非 0）ときも、書けなかった理由の報告は見せる。
func TestAFailedApprovalStillShowsWhy(t *testing.T) {
	a := newApproveKit(t)
	a.worktree(parentID, false)
	report := `{"version":1,"placed":[],"written":[],` +
		`"failed":[{"ticket":"i0001","reason":"提案の内容が更新されました"}],"unwritten":[],"rejected":[]}`
	r := a.approve(map[string]string{"STUB_EXIT": "1", "STUB_REPORT": report})
	code(t, r, 1)
	has(t, r.Stderr, "提案の内容が更新されました")
}

// コミットや push の失敗で承認が失敗に見えないように、承認が通れば 0。コミットは残る。
func TestAFailedPushDoesNotFailTheApproval(t *testing.T) {
	a := newApproveKit(t)
	a.git(a.ws, "remote", "set-url", "origin", filepath.Join(a.tmp, "missing.git"))
	tree := a.worktree(parentID, false)
	code(t, a.approve(map[string]string{"STUB_TREE": shkit.Slash(tree)}), 0)
	eq(t, "件名", a.subject(tree), carryMessage)
}
