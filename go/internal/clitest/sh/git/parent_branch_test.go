package git

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/hoststub"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
)

// `/` を含む既存のブランチ（feature/123-login）で、識別子 feature-123-login の親チケットの作業が、承認 → 着手 → push → 取り込み → 終える
// と通ること。親のブランチ名は親のワークツリーの HEAD から読む。使い捨てのワークスペースと bare のリモートを組み、sh（ccnavi-git.sh・
// ccnavi-sync.sh・ccnavi-ticket.sh・ccnavi-fetch.sh）を外から呼ぶ。ユーザが既に作ったブランチ feature/123-login を、
// .claude/worktrees/feature-123-login が直接チェックアウトする。
//
// 見るのは次のとおり。
//  1. worktree add は、行き先の名前がブランチ名の `/` を `-` に置き換えたものになる形だけを通す。識別子のブランチは作らず、移る手順（switch）も無い
//  2. 承認画面にブランチの行は出ず、既存のブランチとぶつかる warn も出ない
//  3. 最初の push は取り込み状態を作らない
//  4. 取り込み（ccnavi-sync.sh <識別子>）・着手と終える（write-and-push）・セッションの頭の早送り（ccnavi-fetch.sh）が、HEAD から読んだ親のブランチ名の ref を使う
//  5. 親のワークツリーでは、今のブランチと、変換するとその識別子になるブランチのほかへ移れない
//  6. 子のワークツリーからは送れない（子のブランチは子の識別子）
//  7. HEAD が合わない・branch: の欄が残る（エラー）場合の動き。古い取り込み状態が残っていても判定に使わない
//  8. リモートから消えた親のブランチへは、記録が無くても送れない（push のゲート）。一度も送っていない親の最初の push は通す
//     （承認しただけ・着手済みなどの形跡の読み分けは Go の shask.TestPushTraceWithoutTheRemoteReference が見る）

const (
	pbParent   = "feature-123-login"
	pbBranch   = "feature/123-login"
	pbChild    = pbParent + "-01-01"
	pbApproved = ".ccnavi/approved"
	pbTodo     = "wip/proposals/todo"
)

// pbExe は実行ファイルの代わり。`user` の入口は、ラッパーのオプション（--root など）の後の最初の語なので、ターミナル要求を切る
// オプションは `user` の呼び出しでは後ろに付ける。
const pbExe = `#!/bin/sh
skip=""; first=""
for a in "$@"; do
  if [ -n "$skip" ]; then skip=""; continue; fi
  case "$a" in
  --root | --cwd | --record-writes | --record-tree) skip=1 ;;
  --root=* | --cwd=* | --record-writes=* | --record-tree=*) ;;
  *) first="$a"; break ;;
  esac
done
if [ "$first" = user ]; then
  exec 'EXE' "$@" --guard-ticket-approval disable
fi
exec 'EXE' --guard-ticket-approval disable "$@"
`

type pb struct {
	t       *testing.T
	tmp     string
	ws      string
	scripts string
	remote  string
	bin     string
	state   string
	tree    string
	stub    hoststub.Stub
}

func newPB(t *testing.T) *pb {
	t.Helper()
	need(t, "sh", "git")
	tmp := base.TempDir(t)
	p := &pb{t: t, tmp: tmp, ws: filepath.Join(tmp, "ws")}
	p.scripts = filepath.Join(p.ws, ".ccnavi", "scripts")
	src := base.ScriptsDir()
	entries, err := os.ReadDir(src)
	if err != nil {
		t.Fatal(err)
	}
	var shs []string
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".sh") {
			shs = append(shs, e.Name())
		}
	}
	copyInto(t, p.scripts, shs...)
	initRepo(t, p.ws, "main")
	write(t, filepath.Join(p.ws, ".gitignore"), ".claude/worktrees/\nlogs/\n")
	write(t, base.CommonPath(p.ws, "rules"), samples.Rules)
	write(t, base.ConfigPath(p.ws, "phases"), samples.Phases)
	write(t, filepath.Join(p.ws, "src", "keep.py"), "print(1)\n")
	git(t, p.ws, "add", "-A")
	git(t, p.ws, "commit", "-q", "-m", "seed")
	p.remote = filepath.Join(tmp, "origin.git")
	git(t, tmp, "init", "-q", "--bare", "-b", "main", p.remote)
	git(t, p.ws, "remote", "add", "origin", p.remote)
	git(t, p.ws, "push", "-q", "-u", "origin", "main")
	git(t, p.ws, "remote", "set-head", "origin", "main")
	p.bin = writeExec(t, filepath.Join(tmp, "bin", "ccnavi"), strings.ReplaceAll(pbExe, "EXE", exe.ShPath(t)))
	p.state = filepath.Join(p.ws, "logs", "state")
	// ホストのスタブ（git remote get-url だけがホストの形を答える。本物のネットワークには出ない）
	p.stub = hoststub.Build(t, tmp, true)
	p.tree = filepath.Join(p.ws, ".claude", "worktrees", pbParent)
	// ユーザが既に作って送ってあるブランチ（`/` を含む）。
	p.remoteCommit("src/login.py", "print('login')\n", pbBranch, "main")
	// worktree add は、統合先から切るとき最後の fetch が新しいことを求める。
	git(t, p.ws, "fetch", "-q", "origin")
	return p
}

// ---- 道具

func (p *pb) env(extra map[string]string) map[string]string {
	env := map[string]string{"CCNAVI_ENTRY_PATH": p.bin, "CCNAVI_LOCK_WAIT": "2"}
	for k, v := range extra {
		env[k] = v
	}
	return env
}

func (p *pb) exe(args ...string) run.Result {
	p.t.Helper()
	return run.Sh(p.t, p.bin, append([]string{"--root", p.ws}, args...), run.Opts{Dir: p.ws, Env: p.env(nil)})
}

func (p *pb) sh(name string, cwd string, args ...string) run.Result {
	p.t.Helper()
	if cwd == "" {
		cwd = p.ws
	}
	var extra map[string]string
	if name == "ccnavi-ticket.sh" {
		extra = p.stub.Env(hoststub.EnvOpts{})
	}
	return run.Sh(p.t, filepath.Join(p.scripts, name), args, run.Opts{Dir: cwd, Env: p.env(extra)})
}

func (p *pb) ok(r run.Result) run.Result {
	p.t.Helper()
	if r.Code != 0 {
		p.t.Fatalf("code = %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	return r
}

func (p *pb) refused(r run.Result) run.Result {
	p.t.Helper()
	if r.Code != 2 {
		p.t.Fatalf("code = %d (2 のはず)\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	return r
}

func (p *pb) sha(tree, ref string) string {
	out, code := gitTry(tree, "rev-parse", "--verify", "-q", ref)
	if code != 0 {
		return ""
	}
	return strings.TrimSpace(out)
}

func (p *pb) remoteSha(branch string) string {
	out, _ := gitTry(p.ws, "ls-remote", "origin", "refs/heads/"+branch)
	return strings.SplitN(out, "\t", 2)[0]
}

// pusher は他の機械・Chrome の代わりの clone。
func (p *pb) pusher() string {
	p.t.Helper()
	path := filepath.Join(p.tmp, "pusher")
	if !isDir(path) {
		git(p.t, p.tmp, "clone", "-q", p.remote, path)
		identity(p.t, path)
	}
	git(p.t, path, "fetch", "-q", "--prune", "origin")
	return path
}

// deleteRemoteBranch はホストがブランチを消した（他の機械・マージ後の削除）ように見せる。
func (p *pb) deleteRemoteBranch() {
	p.t.Helper()
	git(p.t, p.pusher(), "push", "-q", "origin", "--delete", pbBranch)
}

// remoteCommit はリモートの branch に 1 件積む（start はその枝の元）。
func (p *pb) remoteCommit(rel, text, branch, start string) string {
	p.t.Helper()
	path := p.pusher()
	if start == "" {
		start = branch
	}
	git(p.t, path, "checkout", "-q", "-B", branch, "origin/"+start)
	write(p.t, filepath.Join(path, rel), text)
	git(p.t, path, "add", "--", rel)
	git(p.t, path, "commit", "-q", "-m", "remote "+rel)
	git(p.t, path, "push", "-q", "origin", branch)
	return p.sha(path, "HEAD")
}

func (p *pb) headBranch(tree string) string {
	return gitT(p.t, tree, "rev-parse", "--abbrev-ref", "HEAD")
}

// addParentTree は既存のブランチを直接チェックアウトして親のワークツリーを切り、提案と子の提案を書いてコミットする。
func (p *pb) addParentTree() {
	p.t.Helper()
	p.ok(p.sh("ccnavi-git.sh", "", "fetch", "origin", pbBranch))
	p.ok(p.sh("ccnavi-git.sh", "", "worktree", "add", ".claude/worktrees/"+pbParent, pbBranch))
	p.propose(p.tree, "")
}

func (p *pb) propose(tree, text string) {
	p.t.Helper()
	if text == "" {
		text = samples.ParentText(pbParent, samples.Plan("design"), samples.ParentOpts{Allow: []string{"src/*", "wip/*"}})
	}
	write(p.t, filepath.Join(tree, pbTodo, pbParent+".md"), text)
	write(p.t, filepath.Join(tree, pbTodo, pbChild+".md"), samples.ChildTextReview(pbChild, pbParent, 1, []string{"wip/design/*"}, samples.Off))
	git(p.t, tree, "add", "-A")
	git(p.t, tree, "commit", "-q", "-m", "approval-request")
}

func (p *pb) approve() {
	p.t.Helper()
	preview := p.ok(p.exe("--agree", "--preview", "--json"))
	var body struct {
		Digest string `json:"digest"`
	}
	if err := json.Unmarshal([]byte(preview.Stdout), &body); err != nil {
		p.t.Fatalf("JSON でない: %v\n%s", err, preview.Stdout)
	}
	p.ok(p.exe("user", "agree", "--yes", pbParent+","+pbChild, "--digest", body.Digest, "--json"))
	git(p.t, p.tree, "add", "-A")
	git(p.t, p.tree, "commit", "-q", "-m", "approve")
}

// importedFamily は承認して送り、取り込み済みにした親子のチケット。
func (p *pb) importedFamily() {
	p.t.Helper()
	p.addParentTree()
	p.approve()
	p.ok(p.sh("ccnavi-git.sh", p.tree, "push", "-u", "origin", pbBranch))
	p.ok(p.sh("ccnavi-sync.sh", "", pbParent))
}

// withBranch は親の提案の frontmatter に branch: を足す。
func withBranch(text, branch string) string {
	parts := strings.SplitN(text, "\n", 4)
	return strings.Join(append(parts[:3:3], "branch: "+branch, parts[3]), "\n")
}

// ---- 主な経路

func TestParentBranchTheMainPathOnAnExistingBranchWithASlash(t *testing.T) {
	t.Parallel()
	p := newPB(t)
	has := func(r run.Result, want string) {
		t.Helper()
		if !strings.Contains(r.Stdout, want) {
			t.Errorf("stdout に %q が無い:\n%s", want, r.Stdout)
		}
	}

	// 1. 行き先の名前は、ブランチ名の / を - に置き換えたもの。違う名前は断る
	p.ok(p.sh("ccnavi-git.sh", "", "fetch", "origin", pbBranch))
	for _, args := range [][]string{
		{"worktree", "add", ".claude/worktrees/other", pbBranch},
		{"worktree", "add", ".claude/worktrees/x", "-b", "hotfix/45", "main"},
	} {
		refused := p.refused(p.sh("ccnavi-git.sh", "", args...))
		if !strings.Contains(refused.Stderr, "/ を - に置き換えたもの") {
			t.Errorf("%v: stderr = %q", args, refused.Stderr)
		}
	}
	p.addParentTree()
	if got := p.headBranch(p.tree); got != pbBranch {
		t.Fatalf("HEAD = %q", got)
	}
	if p.sha(p.ws, "refs/heads/"+pbParent) != "" {
		t.Error("識別子のブランチを作った")
	}

	// 2. 親のブランチ名は HEAD から読む。承認画面にブランチの行は出ず、ぶつかりの warn も出ない
	family := p.ok(p.exe("write-and-push", "family", pbParent))
	has(family, "branch "+pbBranch+"\n")
	verify := p.exe("--agree", "--preview", "--verify", "--json")
	var body struct {
		Batch          []map[string]any `json:"batch"`
		Text           string           `json:"text"`
		BranchWarnings []any            `json:"branch_warnings"`
	}
	if err := json.Unmarshal([]byte(verify.Stdout), &body); err != nil {
		t.Fatalf("JSON でない: %v\n%s", err, verify.Stdout)
	}
	found := false
	for _, e := range body.Batch {
		if e["ticket"] == pbParent {
			found = true
			if _, has := e["branch"]; has {
				t.Error("承認画面の親の項目にブランチが出ている")
			}
		}
	}
	if !found || strings.Contains(body.Text, "■ ブランチ") || len(body.BranchWarnings) != 0 {
		t.Errorf("承認画面: found = %v, warnings = %v, text = %q", found, body.BranchWarnings, body.Text)
	}
	p.approve()
	if !isFile(filepath.Join(p.tree, pbApproved, "doing", pbParent+".md")) || !isFile(filepath.Join(p.tree, "src", "login.py")) {
		t.Error("承認済みのコピーか取り込んだファイルが無い")
	}
	if status := git(t, p.tree, "status", "--porcelain"); status != "" {
		t.Errorf("status = %q", status)
	}

	// 3. 最初の push は、リモートにブランチが在るのでゲートを通る。取り込み状態は作らない
	pushed := p.ok(p.sh("ccnavi-git.sh", p.tree, "push", "-u", "origin", pbBranch))
	if strings.Contains(pushed.Stdout, "取り込み状態") || exists(filepath.Join(p.state, "sync")) {
		t.Error("最初の push が取り込み状態を作った")
	}
	if p.remoteSha(pbBranch) != p.sha(p.tree, "HEAD") {
		t.Error("リモートと HEAD が合わない")
	}
	has(p.ok(p.exe("write-and-push", "family", pbParent)), "target yes\n")

	// 4. 取り込みと着手（write-and-push は親のブランチ名へ送る）
	has(p.ok(p.sh("ccnavi-sync.sh", "", pbParent)), "リモートと同じ")
	has(p.ok(p.sh("ccnavi-ticket.sh", "", "start", pbParent)), "に着手した")
	if p.remoteSha(pbBranch) != p.sha(p.tree, "HEAD") || p.remoteSha(pbParent) != "" {
		t.Error("着手の push の行き先が親のブランチ名でない")
	}

	// 5. 親のワークツリーでは、今のブランチのほかへ移れない。今のブランチへは移れる
	moved := p.refused(p.sh("ccnavi-git.sh", p.tree, "switch", "--create", "other"))
	if !strings.Contains(moved.Stderr, "別のブランチ（other）へ移れません") {
		t.Errorf("stderr = %q", moved.Stderr)
	}
	p.ok(p.sh("ccnavi-git.sh", p.tree, "switch", pbBranch))
	if got := p.headBranch(p.tree); got != pbBranch {
		t.Errorf("HEAD = %q", got)
	}

	// 6. 子は子の識別子のブランチで、親のブランチから切る。子からは送らない
	p.ok(p.sh("ccnavi-git.sh", "", "worktree", "add", ".claude/worktrees/"+pbChild, "-b", pbChild, pbBranch))
	childTree := filepath.Join(p.ws, ".claude", "worktrees", pbChild)
	p.ok(p.sh("ccnavi-ticket.sh", "", "start", pbChild))
	write(t, filepath.Join(childTree, "wip", "design", "plan.md"), "# 設計\n")
	git(t, childTree, "add", "-A")
	git(t, childTree, "commit", "-q", "-m", "design")
	p.refused(p.sh("ccnavi-git.sh", childTree, "push", "-u", "origin", pbChild))
	finished := p.ok(p.sh("ccnavi-ticket.sh", "", "finish", pbChild))
	if !isFile(filepath.Join(p.tree, "wip", "proposals", "review", pbChild+".md")) {
		t.Errorf("finish がレビュー待ちを作っていない:\n%s%s", finished.Stdout, finished.Stderr)
	}
	if p.remoteSha(pbBranch) != p.sha(p.tree, "HEAD") {
		t.Error("finish の push が親のブランチへ届いていない")
	}

	// 7. 他の機械の push を取り込む（ccnavi-sync.sh とセッションの頭の早送り）
	p.remoteCommit("wip/note-1.md", "1\n", pbBranch, "")
	has(p.ok(p.sh("ccnavi-sync.sh", "", pbParent)), "早送りで取り込んだ")
	if !isFile(filepath.Join(p.tree, "wip", "note-1.md")) {
		t.Error("note-1.md が取り込まれていない")
	}
	remote := p.remoteCommit("wip/note-2.md", "2\n", pbBranch, "")
	has(p.ok(p.sh("ccnavi-fetch.sh", "")), pbParent+": 親ブランチ "+pbBranch+" を 1 コミット進めた")
	if p.sha(p.tree, "HEAD") != remote || exists(filepath.Join(p.state, "sync")) {
		t.Error("早送りされていないか、取り込み状態ができた")
	}
	lint := p.exe("--lint")
	if strings.Contains(lint.Stdout+lint.Stderr, "の上にある") {
		t.Errorf("lint: %s%s", lint.Stdout, lint.Stderr)
	}
}

func TestParentBranchANewBranchWithASlashIsCutIntoTheConvertedName(t *testing.T) {
	t.Parallel()
	p := newPB(t)
	p.ok(p.sh("ccnavi-git.sh", "", "worktree", "add", ".claude/worktrees/hotfix-45", "-b", "hotfix/45", "main"))
	tree := filepath.Join(p.ws, ".claude", "worktrees", "hotfix-45")
	if got := p.headBranch(tree); got != "hotfix/45" {
		t.Errorf("HEAD = %q", got)
	}
	if p.sha(p.ws, "refs/heads/hotfix-45") != "" {
		t.Error("変換した名前のブランチを作った")
	}
}

// ccnavi-start.sh はチケットの前に ccnavi-sync.sh を打つ。チケットが無いので取り込まない（終了コード 4）が、最初の承認（--agree）の前に
// ccnavi-sync.sh を打ち直さなくても通る。取り込み状態も統合先のコピーも作らない。
func TestParentBranchAParentWorktreeBeforeTheTicketDoesNotStopTheApproval(t *testing.T) {
	t.Parallel()
	p := newPB(t)
	p.ok(p.sh("ccnavi-git.sh", "", "fetch", "origin", pbBranch))
	p.ok(p.sh("ccnavi-git.sh", "", "worktree", "add", ".claude/worktrees/"+pbParent, pbBranch))
	synced := p.sh("ccnavi-sync.sh", "", pbParent)
	if synced.Code != 4 || !strings.Contains(synced.Stdout, "親チケットも提案もありません") || exists(filepath.Join(p.state, "sync")) {
		t.Errorf("code = %d\n%s%s", synced.Code, synced.Stdout, synced.Stderr)
	}
	p.propose(p.tree, "")
	var body struct {
		Verify struct {
			OK bool `json:"ok"`
		} `json:"verify"`
	}
	verify := p.exe("--agree", "--preview", "--verify", "--json")
	if err := json.Unmarshal([]byte(verify.Stdout), &body); err != nil || !body.Verify.OK {
		t.Fatalf("verify = %v: %s", err, verify.Stdout)
	}
	p.approve()
	p.ok(p.sh("ccnavi-git.sh", p.tree, "push", "-u", "origin", pbBranch))
	if exists(filepath.Join(p.state, "sync")) {
		t.Error("取り込み状態を作った")
	}
}

func TestParentBranchALeftOverBranchFieldIsAnError(t *testing.T) {
	t.Parallel()
	p := newPB(t)
	p.addParentTree()
	// 承認済みの親チケット（古い branch: 付き）を手で書く。
	write(t, filepath.Join(p.tree, pbApproved, "doing", pbParent+".md"),
		withBranch(samples.ParentText(pbParent, samples.Plan("design"), samples.ParentOpts{Allow: []string{"src/*"}}), "origin/main"))
	git(t, p.tree, "add", "-A")
	git(t, p.tree, "commit", "-q", "-m", "copy")
	lint := p.exe("--lint")
	if !strings.Contains(lint.Stdout, "`branch` は指定できない欄") {
		t.Errorf("lint: %s%s", lint.Stdout, lint.Stderr)
	}
}

// ---- HEAD が合わない

func TestParentBranchAHeadOffTheConvertedNameStopsTheFamily(t *testing.T) {
	t.Parallel()
	p := newPB(t)
	p.importedFamily()
	git(t, p.tree, "checkout", "-q", "-b", "elsewhere")
	family := p.ok(p.exe("write-and-push", "family", pbParent))
	if !strings.Contains(family.Stdout, "target stop\n") || !strings.Contains(family.Stdout, "branch_refused ") || strings.Contains(family.Stdout, "\nbranch ") {
		t.Errorf("family:\n%s", family.Stdout)
	}
	synced := p.sh("ccnavi-sync.sh", "", pbParent)
	if synced.Code != 1 || !strings.Contains(synced.Stdout, "と合わない") {
		t.Errorf("code = %d\n%s%s", synced.Code, synced.Stdout, synced.Stderr)
	}
	// 全部を回すときは、合わないワークツリーを飛ばす
	p.ok(p.sh("ccnavi-sync.sh", ""))
	// 戻せば動く
	git(t, p.tree, "checkout", "-q", pbBranch)
	p.ok(p.sh("ccnavi-sync.sh", "", pbParent))
}

func TestParentBranchSyncingAFamilyWithoutItsWorktreeSaysSo(t *testing.T) {
	t.Parallel()
	p := newPB(t)
	synced := p.sh("ccnavi-sync.sh", "", "feature-99-none")
	if synced.Code != 1 || !strings.Contains(synced.Stdout, "親のワークツリー（.claude/worktrees/feature-99-none）がありません") {
		t.Errorf("code = %d\n%s%s", synced.Code, synced.Stdout, synced.Stderr)
	}
}

// ---- 止める形

// リモートから消えた親のブランチへは、記録が無くても送れない（push のゲート）。ユーザが元の名前で戻せば、また送れる。
func TestParentBranchAGoneParentBranchCannotBePushed(t *testing.T) {
	t.Parallel()
	p := newPB(t)
	p.importedFamily()
	p.deleteRemoteBranch()
	refused := p.refused(p.sh("ccnavi-git.sh", p.tree, "push", "-u", "origin", pbBranch))
	if !strings.Contains(refused.Stderr, "親のブランチ "+pbBranch+" がリモートに無い") || !strings.Contains(refused.Stderr, "ccnavi-sync.sh "+pbParent) || p.remoteSha(pbBranch) != "" {
		t.Errorf("stderr = %q, remote = %q", refused.Stderr, p.remoteSha(pbBranch))
	}
	// 診断は ccnavi-sync.sh が出す（MR の答えはスタブ。マージされていない）。
	writeExec(t, filepath.Join(p.scripts, "ccnavi-review.sh"), "#!/bin/sh\n[ \"$1\" = merged ] && echo none\nexit 0\n")
	synced := p.sh("ccnavi-sync.sh", "", pbParent)
	if synced.Code != 1 || !strings.Contains(synced.Stdout, "戻し方 1") {
		t.Errorf("code = %d\n%s%s", synced.Code, synced.Stdout, synced.Stderr)
	}
	// ユーザがターミナルで、生の git で元の名前に戻す。
	git(t, p.tree, "push", "-q", "origin", pbBranch)
	if p.remoteSha(pbBranch) != p.sha(p.tree, "HEAD") {
		t.Error("元の名前に戻っていない")
	}
	p.ok(p.sh("ccnavi-git.sh", p.tree, "push", "-u", "origin", pbBranch))
}

// 一度も送っていない親（参照も承認済みチケットも依頼も無い）は、最初の push を通す。
func TestParentBranchABranchNeverPushedIsPushedOnce(t *testing.T) {
	t.Parallel()
	p := newPB(t)
	p.ok(p.sh("ccnavi-git.sh", "", "fetch", "origin", pbBranch))
	p.ok(p.sh("ccnavi-git.sh", "", "worktree", "add", ".claude/worktrees/"+pbParent, pbBranch))
	p.propose(p.tree, "")
	p.deleteRemoteBranch()
	git(t, p.ws, "update-ref", "-d", "refs/remotes/origin/"+pbBranch)
	p.ok(p.sh("ccnavi-git.sh", p.tree, "push", "-u", "origin", pbBranch))
	if p.remoteSha(pbBranch) != p.sha(p.tree, "HEAD") {
		t.Error("最初の push が届いていない")
	}
}

// fetch.prune で参照（origin/ブランチ）が消えても、着手済み（started_at あり）の親は、リモートに無いブランチを作り直さない。
func TestParentBranchAStartedParentWhoseReferenceWasPrunedIsStillGuarded(t *testing.T) {
	t.Parallel()
	p := newPB(t)
	p.importedFamily()
	copyPath := filepath.Join(p.tree, pbApproved, "doing", pbParent+".md")
	// 承認済みのコピーは、着手前は started_at: "" を持つ。着手済みの値に書き換える。
	text := read(t, copyPath)
	if !strings.Contains(text, `started_at: ""`) {
		t.Fatal(`承認済みのコピーに started_at: "" が無い`)
	}
	write(t, copyPath, strings.Replace(text, `started_at: ""`, "started_at: 2026-10-18T00:00:00Z", 1))
	git(t, p.tree, "add", "-A")
	git(t, p.tree, "commit", "-q", "-m", "start")
	p.deleteRemoteBranch()
	git(t, p.ws, "update-ref", "-d", "refs/remotes/origin/"+pbBranch)
	refused := p.refused(p.sh("ccnavi-git.sh", p.tree, "push", "-u", "origin", pbBranch))
	if !strings.Contains(refused.Stderr, "親のブランチ "+pbBranch+" がリモートに無い") || p.remoteSha(pbBranch) != "" {
		t.Errorf("stderr = %q, remote = %q", refused.Stderr, p.remoteSha(pbBranch))
	}
}
